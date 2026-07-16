from __future__ import annotations

import os
import threading
import uuid
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .batch_store import BatchStore
from .jobs import JobManager
from .models import (
    BatchConfigSnapshot,
    BatchCreateRequest,
    BatchCreateResult,
    BatchDeleteResult,
    BatchJobCreateResult,
    BatchPreflightIssue,
    BatchPreflightRequest,
    BatchPreflightResult,
    BatchRecord,
    BatchRunRequest,
    BatchSourcePreflightView,
    BatchSourceRequest,
    BatchStatusSummary,
    JobBulkAction,
    JobBulkActionRequest,
    JobBulkActionResponse,
    JobBulkActionResult,
    JobCreateRequest,
    JobRunRequest,
    JobStatus,
    TranslationProviderName,
    TranslationSettings,
    utc_now,
)


@dataclass
class _ResolvedBatchSource:
    index: int
    request: JobCreateRequest
    view: BatchSourcePreflightView
    source_key: str
    output_key: str


@dataclass
class _PreflightBundle:
    result: BatchPreflightResult
    resolved: dict[int, _ResolvedBatchSource]


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class BatchManager:
    """Coordinate persisted Batch groups without coupling Job lifecycles together."""

    def __init__(self, store: BatchStore, jobs: JobManager) -> None:
        self.store = store
        self.jobs = jobs
        self._lock = threading.RLock()
        self._batches = {batch.id: batch for batch in store.load()}
        self._prune_missing_jobs()

    def _prune_missing_jobs(self) -> None:
        for batch in self._batches.values():
            existing: list[str] = []
            for job_id in batch.job_ids:
                try:
                    self.jobs.get_summary(job_id)
                except KeyError:
                    continue
                existing.append(job_id)
            if existing != batch.job_ids:
                batch.job_ids = existing
                batch.updated_at = utc_now()
                batch.status_summary = self._view(batch).status_summary
                self.store.save(batch)

    def _record(self, batch_id: str) -> BatchRecord:
        with self._lock:
            try:
                return self._batches[batch_id]
            except KeyError as exc:
                raise KeyError("批次不存在") from exc

    def _view(self, batch: BatchRecord) -> BatchRecord:
        summaries = []
        for job_id in batch.job_ids:
            try:
                summaries.append(self.jobs.get_summary(job_id))
            except KeyError:
                continue
        counts = {status: 0 for status in JobStatus}
        for summary in summaries:
            counts[summary.status] += 1
        view = batch.model_copy(deep=True)
        view.status_summary = BatchStatusSummary(
            total=len(summaries),
            draft=counts[JobStatus.DRAFT],
            queued=counts[JobStatus.QUEUED],
            running=counts[JobStatus.RUNNING],
            waiting_for_input=counts[JobStatus.WAITING_FOR_INPUT],
            completed=counts[JobStatus.COMPLETED],
            failed=counts[JobStatus.FAILED],
            cancelled=counts[JobStatus.CANCELLED],
            interrupted=counts[JobStatus.INTERRUPTED],
            progress=(
                round(sum(summary.progress for summary in summaries) / len(summaries))
                if summaries
                else 0
            ),
        )
        if summaries:
            view.updated_at = max(
                batch.updated_at,
                *(summary.updated_at for summary in summaries),
            )
        return view

    def list(self) -> list[BatchRecord]:
        with self._lock:
            batches = sorted(
                self._batches.values(),
                key=lambda batch: batch.created_at,
                reverse=True,
            )
            return [self._view(batch) for batch in batches]

    def get(self, batch_id: str) -> BatchRecord:
        return self._view(self._record(batch_id))

    @staticmethod
    def _job_request(
        source: BatchSourceRequest,
        config: BatchConfigSnapshot,
    ) -> JobCreateRequest:
        translation = config.translation
        return JobCreateRequest(
            video_path=source.video_path,
            upload_id=source.upload_id,
            target_language=config.target_language,
            asr=config.asr.model_copy(deep=True),
            translation=TranslationSettings(
                provider=translation.provider,
                model=translation.model,
                endpoint=translation.endpoint,
                timeout_seconds=translation.timeout_seconds,
            ),
            export=(source.export or config.export).model_copy(deep=True),
        )

    def _preflight(self, request: BatchPreflightRequest) -> _PreflightBundle:
        views: list[BatchSourcePreflightView] = []
        resolved: dict[int, _ResolvedBatchSource] = {}
        for index, source in enumerate(request.sources):
            view = BatchSourcePreflightView(
                index=index,
                video_path=source.video_path,
                upload_id=source.upload_id,
                valid=False,
            )
            if bool(source.video_path) == bool(source.upload_id):
                view.issues.append(
                    BatchPreflightIssue(
                        code="invalid_source",
                        message="video_path 与 upload_id 必须且只能填写一个",
                    )
                )
                views.append(view)
                continue
            try:
                job_request = self._job_request(source, request.config)
                media = self.jobs.resolve_source(job_request)
                path = Path(media.path).resolve(strict=True)
                export = job_request.export
                output_directory = (
                    Path(export.output_directory).expanduser().resolve()
                    if export.output_directory
                    else path.parent
                )
                output_path = (output_directory / f"{path.stem}.srt").resolve()
                view.source_name = media.name
                view.normalized_path = str(path)
                view.size = path.stat().st_size
                view.output_path = str(output_path)
                if output_path.exists() and not export.overwrite_existing:
                    view.issues.append(
                        BatchPreflightIssue(
                            code="output_exists",
                            message="目标字幕已存在且当前配置不允许覆盖",
                        )
                    )
                resolved[index] = _ResolvedBatchSource(
                    index=index,
                    request=job_request,
                    view=view,
                    source_key=_path_key(path),
                    output_key=_path_key(output_path),
                )
            except (KeyError, OSError, ValueError) as exc:
                view.issues.append(
                    BatchPreflightIssue(
                        code="invalid_source",
                        message=str(exc) or "源文件无效",
                    )
                )
            views.append(view)

        source_groups: dict[str, list[_ResolvedBatchSource]] = defaultdict(list)
        output_groups: dict[str, list[_ResolvedBatchSource]] = defaultdict(list)
        for item in resolved.values():
            source_groups[item.source_key].append(item)
            output_groups[item.output_key].append(item)
        for group in source_groups.values():
            if len(group) < 2:
                continue
            for item in group:
                item.view.issues.append(
                    BatchPreflightIssue(
                        code="duplicate_source",
                        message="同一规范化路径不能在一个批次中重复创建",
                    )
                )
        for group in output_groups.values():
            if len(group) < 2:
                continue
            for item in group:
                item.view.issues.append(
                    BatchPreflightIssue(
                        code="output_conflict",
                        message="多个任务将写入同一个字幕路径，请调整单项输出目录",
                    )
                )

        for view in views:
            view.valid = not view.issues
        result = BatchPreflightResult(
            items=views,
            valid_count=sum(view.valid for view in views),
            invalid_count=sum(not view.valid for view in views),
            has_output_conflicts=any(
                issue.code == "output_conflict"
                for view in views
                for issue in view.issues
            ),
        )
        return _PreflightBundle(result=result, resolved=resolved)

    def preflight(self, request: BatchPreflightRequest) -> BatchPreflightResult:
        return self._preflight(request).result

    def create(self, request: BatchCreateRequest) -> BatchCreateResult:
        bundle = self._preflight(request)
        results = [
            BatchJobCreateResult(
                index=view.index,
                source_name=view.source_name,
                ok=False,
                error="；".join(issue.message for issue in view.issues),
            )
            for view in bundle.result.items
            if not view.valid
        ]
        valid = [
            bundle.resolved[view.index]
            for view in bundle.result.items
            if view.valid
        ]
        if (
            request.auto_start
            and request.config.translation.provider == TranslationProviderName.DEEPSEEK
            and (
                request.api_key is None
                or not request.api_key.get_secret_value().strip()
            )
        ):
            results.extend(
                BatchJobCreateResult(
                    index=item.index,
                    source_name=item.view.source_name,
                    ok=False,
                    error="DeepSeek 自动启动需要本次运行 API Key",
                )
                for item in valid
            )
            return BatchCreateResult(
                preflight=bundle.result,
                results=sorted(results, key=lambda item: item.index),
                created_count=0,
                failed_count=len(results),
            )
        if not valid:
            return BatchCreateResult(
                preflight=bundle.result,
                results=sorted(results, key=lambda item: item.index),
                created_count=0,
                failed_count=len(results),
            )

        batch = BatchRecord(
            id=uuid.uuid4().hex,
            name=request.name,
            config_template=request.config.model_copy(deep=True),
        )
        with self._lock:
            self._batches[batch.id] = batch
            self.store.save(batch)
        created_count = 0
        for item in valid:
            try:
                translation = item.request.translation.model_copy(
                    update={"api_key": request.api_key}
                )
                job_request = item.request.model_copy(
                    deep=True,
                    update={
                        "translation": translation,
                        "auto_start": request.auto_start,
                    },
                )
                created = self.jobs.create(job_request, batch_id=batch.id)
                with self._lock:
                    batch.job_ids.append(created.id)
                    batch.updated_at = utc_now()
                    batch.status_summary = self._view(batch).status_summary
                    self.store.save(batch)
                created_count += 1
                results.append(
                    BatchJobCreateResult(
                        index=item.index,
                        source_name=item.view.source_name,
                        ok=True,
                        job=self.jobs.get_summary(created.id),
                    )
                )
            except (KeyError, OSError, ValueError) as exc:
                results.append(
                    BatchJobCreateResult(
                        index=item.index,
                        source_name=item.view.source_name,
                        ok=False,
                        error=str(exc),
                    )
                )
        if not batch.job_ids:
            with self._lock:
                self._batches.pop(batch.id, None)
                self.store.delete(batch.id)
            batch_view = None
        else:
            batch_view = self._view(batch)
        return BatchCreateResult(
            batch=batch_view,
            preflight=bundle.result,
            results=sorted(results, key=lambda result: result.index),
            created_count=created_count,
            failed_count=len(results) - created_count,
        )

    def bulk_action(self, request: JobBulkActionRequest) -> JobBulkActionResponse:
        results: list[JobBulkActionResult] = []
        for job_id in request.job_ids:
            try:
                current = self.jobs.get_summary(job_id)
                if request.action == JobBulkAction.RUN:
                    if current.status == JobStatus.COMPLETED:
                        raise ValueError("已完成任务不会被批量重新运行")
                    self.jobs.run(
                        job_id,
                        JobRunRequest(
                            api_key=request.api_key,
                            continue_pipeline=request.continue_pipeline,
                        ),
                    )
                    job = self.jobs.get_summary(job_id)
                elif request.action == JobBulkAction.RETRY_FAILED:
                    if current.status != JobStatus.FAILED:
                        raise ValueError("任务不是 failed 状态")
                    self.jobs.run(
                        job_id,
                        JobRunRequest(
                            api_key=request.api_key,
                            continue_pipeline=request.continue_pipeline,
                        ),
                    )
                    job = self.jobs.get_summary(job_id)
                elif request.action == JobBulkAction.CANCEL:
                    self.jobs.cancel(job_id)
                    job = self.jobs.get_summary(job_id)
                elif request.action == JobBulkAction.UPDATE_CONFIG:
                    assert request.step is not None
                    assert request.config is not None
                    self.jobs.update_step_config(
                        job_id,
                        request.step,
                        request.config,
                    )
                    job = self.jobs.get_summary(job_id)
                else:
                    self.jobs.delete(job_id)
                    self.remove_job(job_id)
                    job = None
                results.append(JobBulkActionResult(job_id=job_id, ok=True, job=job))
            except (KeyError, OSError, ValueError) as exc:
                try:
                    job = self.jobs.get_summary(job_id)
                except KeyError:
                    job = None
                results.append(
                    JobBulkActionResult(
                        job_id=job_id,
                        ok=False,
                        job=job,
                        error=(str(exc) or type(exc).__name__)[:500],
                    )
                )
        succeeded = sum(result.ok for result in results)
        return JobBulkActionResponse(
            action=request.action,
            results=results,
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )

    def batch_action(
        self,
        batch_id: str,
        action: JobBulkAction,
        request: BatchRunRequest | None = None,
    ) -> JobBulkActionResponse:
        batch = self._record(batch_id)
        runtime = request or BatchRunRequest()
        if not batch.job_ids:
            return JobBulkActionResponse(
                action=action,
                results=[],
                succeeded=0,
                failed=0,
            )
        return self.bulk_action(
            JobBulkActionRequest(
                action=action,
                job_ids=list(batch.job_ids),
                api_key=runtime.api_key,
            )
        )

    def remove_job(self, job_id: str) -> None:
        with self._lock:
            for batch in self._batches.values():
                if job_id not in batch.job_ids:
                    continue
                batch.job_ids = [item for item in batch.job_ids if item != job_id]
                batch.updated_at = utc_now()
                batch.status_summary = self._view(batch).status_summary
                self.store.save(batch)

    def delete(self, batch_id: str, *, delete_jobs: bool) -> BatchDeleteResult:
        batch = self._record(batch_id)
        results: list[JobBulkActionResult] = []
        if delete_jobs and batch.job_ids:
            response = self.bulk_action(
                JobBulkActionRequest(
                    action=JobBulkAction.DELETE,
                    job_ids=list(batch.job_ids),
                )
            )
            results.extend(response.results)
        else:
            for job_id in list(batch.job_ids):
                try:
                    job = self.jobs.assign_batch(job_id, None)
                    results.append(
                        JobBulkActionResult(job_id=job_id, ok=True, job=job)
                    )
                except KeyError as exc:
                    results.append(
                        JobBulkActionResult(
                            job_id=job_id,
                            ok=False,
                            error=str(exc),
                        )
                    )
        for result in results:
            if result.ok or result.job is None:
                continue
            with suppress(KeyError):
                result.job = self.jobs.assign_batch(result.job_id, None)
        with self._lock:
            self._batches.pop(batch_id, None)
            self.store.delete(batch_id)
        return BatchDeleteResult(
            batch_id=batch_id,
            deleted=True,
            delete_jobs=delete_jobs,
            results=results,
        )
