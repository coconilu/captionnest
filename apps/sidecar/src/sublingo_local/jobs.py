from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError

from .asr import ASRProvider, FasterWhisperProvider
from .asr.base import TranscriptionResult
from .job_repository import JobRepository
from .job_store import JobStore
from .media import ensure_supported_video
from .model_manager import ModelManager
from .models import (
    ASROutputMode,
    ASRSettings,
    ExportSettings,
    JobCreateRequest,
    JobRunRequest,
    JobStage,
    JobStatus,
    JobStep,
    JobStepView,
    JobSummaryPage,
    JobSummaryView,
    JobView,
    LegacyASRSettings,
    LogEntry,
    LogLevel,
    MediaStepSettings,
    ModelUsageSummary,
    QueueStatus,
    ResolvedSource,
    SchedulerSettings,
    SourceKind,
    StepArtifactView,
    StepAttemptView,
    StepStatus,
    TargetLanguage,
    TranslatedItem,
    TranslationProviderName,
    TranslationStepSettings,
    merge_model_usage,
    utc_now,
)
from .scheduler import JobScheduler
from .storage import UploadStore
from .subtitles import segments_to_translation_items, write_bilingual_srt
from .translation import TranslationService, create_translation_provider

ASRFactory = Callable[[], ASRProvider]
PersistCallback = Callable[[Mapping[str, Any]], None]

STEP_ORDER = (
    JobStep.MEDIA,
    JobStep.TRANSCRIPTION,
    JobStep.TRANSLATION,
    JobStep.EXPORT,
)
STEP_STAGE = {
    JobStep.MEDIA: JobStage.EXTRACTING,
    JobStep.TRANSCRIPTION: JobStage.TRANSCRIBING,
    JobStep.TRANSLATION: JobStage.TRANSLATING,
    JobStep.EXPORT: JobStage.WRITING,
}
STEP_PROGRESS = {
    JobStep.MEDIA: (0, 8),
    JobStep.TRANSCRIPTION: (8, 60),
    JobStep.TRANSLATION: (60, 94),
    JobStep.EXPORT: (94, 100),
}
LEGACY_ASR_UNAVAILABLE_MESSAGE = (
    "Qwen3-ASR 已停用；历史任务仍可查看，请先在识别配置中改选 Faster-Whisper 模型"
)
SUMMARY_SNAPSHOT_TTL_SECONDS = 300.0
SUMMARY_SNAPSHOT_MAX_ENTRIES = 64
SUMMARY_SNAPSHOT_MAX_ITEMS = 20_000


class _BlockingCallCancelled(RuntimeError):
    """Cooperatively stop a blocking provider after its asyncio owner is cancelled."""


def _language_key(language: str | TargetLanguage) -> str:
    normalized = str(language).strip().lower().replace("_", "-")
    return {
        "zh": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "cmn": "zh",
        "eng": "en",
        "kor": "ko",
    }.get(normalized, normalized)


def _redact(value: str, secrets: tuple[str, ...] = ()) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result


def _safe_validation_message(exc: ValidationError) -> str:
    messages = [
        str(error.get("msg", "配置无效")).removeprefix("Value error, ")
        for error in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    ]
    return "；".join(dict.fromkeys(messages)) or "配置无效"


def _fingerprint(payload: Mapping[str, Any]) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def subtitle_output_path(source_path: Path, export: ExportSettings) -> Path:
    try:
        output_directory = (
            Path(export.output_directory).expanduser().resolve()
            if export.output_directory
            else source_path.resolve().parent
        )
        return (output_directory / source_path.with_suffix(".srt").name).resolve()
    except RuntimeError as exc:
        raise ValueError("字幕输出路径无法解析") from exc


def output_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def validate_output_target(path: Path, *, overwrite_existing: bool) -> None:
    """Reject targets that the atomic SRT writer cannot safely replace."""

    resolved = path.resolve()
    ancestor = resolved.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if ancestor.exists() and not ancestor.is_dir():
        raise NotADirectoryError("字幕输出目录不是文件夹")
    if not resolved.exists():
        return
    if not resolved.is_file():
        raise IsADirectoryError("目标字幕路径不是可覆盖的普通文件")
    if not overwrite_existing:
        raise FileExistsError("目标字幕已存在，请允许覆盖或修改输出目录")


@dataclass(frozen=True)
class _SummaryCursor:
    snapshot_id: str
    offset: int
    filter_fingerprint: str


@dataclass(frozen=True)
class _SummarySnapshot:
    snapshot_id: str
    filter_fingerprint: str
    server_time: datetime
    statuses: tuple[JobStatus, ...] | None
    batch_id: str | None
    query: str | None
    updated_after: datetime | None
    items: tuple[JobSummaryView, ...]
    expires_at_monotonic: float


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _summary_filter_payload(
    *,
    statuses: tuple[JobStatus, ...] | None,
    batch_id: str | None,
    query: str | None,
    updated_after: datetime | None,
) -> dict[str, Any]:
    return {
        "statuses": [status.value for status in statuses] if statuses is not None else None,
        "batch_id": batch_id,
        "query": query,
        "updated_after": updated_after.isoformat() if updated_after is not None else None,
    }


def _encode_summary_cursor(
    snapshot: _SummarySnapshot,
    offset: int,
) -> str:
    payload = json.dumps(
        {
            "v": 3,
            "snapshot_id": snapshot.snapshot_id,
            "offset": offset,
            "filter_fingerprint": snapshot.filter_fingerprint,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_summary_cursor(cursor: str) -> _SummaryCursor:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("v") != 3:
            raise ValueError
        snapshot_id = payload["snapshot_id"]
        offset = payload["offset"]
        filter_fingerprint = payload["filter_fingerprint"]
        if not isinstance(snapshot_id, str) or len(snapshot_id) != 32:
            raise ValueError
        if any(character not in "0123456789abcdef" for character in snapshot_id):
            raise ValueError
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            raise ValueError
        if (
            not isinstance(filter_fingerprint, str)
            or len(filter_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in filter_fingerprint
            )
        ):
            raise ValueError
        return _SummaryCursor(
            snapshot_id=snapshot_id,
            offset=offset,
            filter_fingerprint=filter_fingerprint,
        )
    except (
        binascii.Error,
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("分页 cursor 无效") from exc


async def _run_blocking_call(
    function: Callable[..., Any],
    /,
    *args: Any,
    cancel_callback: Callable[[], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Keep a scheduler resource claimed until its worker thread really stops."""

    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        if cancel_callback is not None:
            cancel_callback()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Repeated cancellation must not detach a still-running thread.
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()
        raise


def _default_steps() -> dict[JobStep, JobStepView]:
    return {
        step: JobStepView(id=step, status=StepStatus.PENDING)
        for step in STEP_ORDER
    }


@dataclass
class JobRecord:
    id: str
    media: MediaStepSettings
    asr: ASRSettings | LegacyASRSettings = field(default_factory=ASRSettings)
    translation: TranslationStepSettings = field(default_factory=TranslationStepSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    batch_id: str | None = None
    status: JobStatus = JobStatus.DRAFT
    queue_status: QueueStatus = QueueStatus.DRAFT
    queue_position: int | None = None
    priority: int = 0
    stage: JobStage = JobStage.DRAFT
    progress: int = 0
    detected_language: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    interrupted_at: datetime | None = None
    error: str | None = None
    logs: list[LogEntry] = field(default_factory=list)
    steps: dict[JobStep, JobStepView] = field(default_factory=_default_steps)
    current_step: JobStep | None = None
    queued_start_step: JobStep | None = None
    queued_continue_pipeline: bool = True
    _persist_callback: PersistCallback | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _attempt_started_monotonic: dict[JobStep, float] = field(
        default_factory=dict,
        repr=False,
    )

    @property
    def source(self) -> ResolvedSource:
        return ResolvedSource(
            kind=self.media.source_kind,
            path=Path(self.media.path),
            name=self.media.name,
        )

    @property
    def subtitle_path(self) -> str | None:
        export_step = self.steps[JobStep.EXPORT]
        if export_step.status != StepStatus.SUCCEEDED or not export_step.artifact:
            return None
        return export_step.artifact.path

    def attach_persistence(self, callback: PersistCallback | None) -> None:
        with self._lock:
            self._persist_callback = callback

    def assign_batch(self, batch_id: str | None) -> None:
        with self._lock:
            if self.batch_id == batch_id:
                return
            self.batch_id = batch_id
            self.updated_at = utc_now()
            self._persist()

    def config_model(self, step: JobStep) -> BaseModel:
        return {
            JobStep.MEDIA: self.media,
            JobStep.TRANSCRIPTION: self.asr,
            JobStep.TRANSLATION: self.translation,
            JobStep.EXPORT: self.export,
        }[step]

    def config_payload(self, step: JobStep) -> dict[str, Any]:
        return self.config_model(step).model_dump(mode="json")

    def _persist(self) -> None:
        callback = self._persist_callback
        if callback is not None:
            callback(self.to_payload())

    def _append_log(
        self,
        message: str,
        *,
        level: LogLevel = LogLevel.INFO,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.logs.append(LogEntry(level=level, message=_redact(message, secrets)))
        self.logs = self.logs[-200:]

    def update(
        self,
        *,
        status: JobStatus | None = None,
        stage: JobStage | None = None,
        progress: int | None = None,
        message: str | None = None,
        level: LogLevel = LogLevel.INFO,
    ) -> None:
        """Compatibility helper for simple injected pipelines and existing integrations."""
        with self._lock:
            if status is not None:
                self.status = status
                self.queue_status = QueueStatus(status.value)
            if stage is not None:
                self.stage = stage
            if progress is not None:
                self.progress = max(0, min(100, progress))
            if message:
                self._append_log(message, level=level)
            self.updated_at = utc_now()
            self._persist()

    def set_detected_language(self, language: str) -> None:
        with self._lock:
            self.detected_language = language
            self.updated_at = utc_now()
            self._persist()

    def begin_step(self, step: JobStep, message: str) -> None:
        with self._lock:
            state = self.steps[step]
            started_at = utc_now()
            self._attempt_started_monotonic[step] = time.perf_counter()
            attempt = StepAttemptView(
                number=len(state.attempts) + 1,
                status=StepStatus.RUNNING,
                config=self.config_payload(step),
                started_at=started_at,
            )
            state.attempts.append(attempt)
            state.status = StepStatus.RUNNING
            state.progress = 0
            state.error = None
            self.current_step = step
            self.status = JobStatus.RUNNING
            self.queue_status = QueueStatus.RUNNING
            self.queue_position = None
            self.stage = STEP_STAGE[step]
            self.error = None
            self.progress = STEP_PROGRESS[step][0]
            self._append_log(message)
            self.updated_at = utc_now()
            self._persist()

    def record_model_usage(self, step: JobStep, usage: ModelUsageSummary) -> None:
        """Persist one model-call delta while its Attempt is still active."""
        with self._lock:
            state = self.steps[step]
            if not state.attempts or state.attempts[-1].status != StepStatus.RUNNING:
                return
            attempt = state.attempts[-1]
            current = [attempt.model_usage] if attempt.model_usage is not None else []
            attempt.model_usage = merge_model_usage([*current, usage])
            self.updated_at = utc_now()
            self._persist()

    def _finish_attempt_timing(
        self,
        step: JobStep,
        attempt: StepAttemptView,
    ) -> None:
        attempt.finished_at = utc_now()
        started = self._attempt_started_monotonic.pop(step, None)
        if started is not None:
            elapsed_ms = round((time.perf_counter() - started) * 1_000)
            attempt.duration_ms = max(0, elapsed_ms)

    def update_step_progress(self, step: JobStep, value: float) -> None:
        normalized = max(0.0, min(1.0, value))
        with self._lock:
            state = self.steps[step]
            state.progress = round(normalized * 100)
            start, end = STEP_PROGRESS[step]
            self.progress = start + round((end - start) * normalized)
            self.updated_at = utc_now()
            self._persist()

    def complete_step(
        self,
        step: JobStep,
        artifact: StepArtifactView,
        message: str,
    ) -> None:
        with self._lock:
            state = self.steps[step]
            state.status = StepStatus.SUCCEEDED
            state.progress = 100
            state.error = None
            state.artifact = artifact
            attempt = state.attempts[-1]
            attempt.status = StepStatus.SUCCEEDED
            self._finish_attempt_timing(step, attempt)
            attempt.artifact_id = artifact.id
            self.progress = STEP_PROGRESS[step][1]
            self.current_step = None
            self._append_log(message)
            self.updated_at = utc_now()
            self._persist()

    def fail_current(self, exc: Exception, *, secrets: tuple[str, ...] = ()) -> None:
        message = _redact(str(exc) or type(exc).__name__, secrets)
        with self._lock:
            step = self.current_step
            if step is None:
                step = next(
                    (
                        item
                        for item in STEP_ORDER
                        if self.steps[item].status != StepStatus.SUCCEEDED
                    ),
                    JobStep.MEDIA,
                )
            state = self.steps[step]
            state.status = StepStatus.FAILED
            state.error = message
            if state.attempts and state.attempts[-1].status == StepStatus.RUNNING:
                attempt = state.attempts[-1]
                attempt.status = StepStatus.FAILED
                self._finish_attempt_timing(step, attempt)
                attempt.error = message
            self.current_step = None
            self.status = JobStatus.FAILED
            self.queue_status = QueueStatus.FAILED
            self.queue_position = None
            self.stage = STEP_STAGE[step]
            self.error = message
            self._append_log(message, level=LogLevel.ERROR)
            self.updated_at = utc_now()
            self._persist()

    def cancel_current(self) -> None:
        with self._lock:
            step = self.current_step
            if step is not None:
                state = self.steps[step]
                state.status = StepStatus.CANCELLED
                state.error = "任务已取消"
                if state.attempts and state.attempts[-1].status == StepStatus.RUNNING:
                    attempt = state.attempts[-1]
                    attempt.status = StepStatus.CANCELLED
                    self._finish_attempt_timing(step, attempt)
                    attempt.error = "任务已取消"
            self.current_step = None
            self.status = JobStatus.CANCELLED
            self.queue_status = QueueStatus.CANCELLED
            self.queue_position = None
            self.stage = JobStage.CANCELLED
            self.error = "任务已取消"
            self._append_log("任务已取消", level=LogLevel.WARNING)
            self.updated_at = utc_now()
            self._persist()

    def invalidate_from(self, step: JobStep, *, message: str | None = None) -> None:
        with self._lock:
            previous = self.to_payload()
            self._invalidate_from_locked(step, message=message)
            try:
                self._persist()
            except Exception:
                self._restore_payload_locked(previous)
                raise

    def _invalidate_from_locked(
        self,
        step: JobStep,
        *,
        message: str | None = None,
    ) -> None:
        if self.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise ValueError("任务运行中，不能修改配置或重置步骤")
        start_index = STEP_ORDER.index(step)
        for item in STEP_ORDER[start_index:]:
            state = self.steps[item]
            state.status = StepStatus.STALE if state.artifact else StepStatus.PENDING
            state.progress = 0
            state.error = None
        self.status = JobStatus.DRAFT
        self.queue_status = QueueStatus.DRAFT
        self.queue_position = None
        self.stage = STEP_STAGE[step]
        self.progress = STEP_PROGRESS[step][0]
        self.error = None
        self.current_step = None
        if message:
            self._append_log(message)
        self.updated_at = utc_now()

    def prepare_queue(
        self,
        step: JobStep,
        *,
        continue_pipeline: bool,
        queue_position: int,
    ) -> None:
        """Persist invalidation and queued state as one recoverable transition."""

        with self._lock:
            previous = self.to_payload()
            self._invalidate_from_locked(step)
            self._mark_queued_locked(
                step,
                continue_pipeline=continue_pipeline,
                queue_position=queue_position,
            )
            try:
                self._persist()
            except Exception:
                self._restore_payload_locked(previous)
                raise

    def _restore_payload_locked(self, payload: Mapping[str, Any]) -> None:
        restored = type(self).from_payload(payload)
        for name in (
            "media",
            "asr",
            "translation",
            "export",
            "batch_id",
            "status",
            "queue_status",
            "queue_position",
            "priority",
            "stage",
            "progress",
            "detected_language",
            "created_at",
            "updated_at",
            "interrupted_at",
            "error",
            "logs",
            "steps",
            "current_step",
            "queued_start_step",
            "queued_continue_pipeline",
        ):
            setattr(self, name, getattr(restored, name))
        self._attempt_started_monotonic.clear()

    def mark_queued(
        self,
        step: JobStep,
        *,
        continue_pipeline: bool,
        queue_position: int,
    ) -> None:
        with self._lock:
            previous = self.to_payload()
            self._mark_queued_locked(
                step,
                continue_pipeline=continue_pipeline,
                queue_position=queue_position,
            )
            try:
                self._persist()
            except Exception:
                self._restore_payload_locked(previous)
                raise

    def _mark_queued_locked(
        self,
        step: JobStep,
        *,
        continue_pipeline: bool,
        queue_position: int,
    ) -> None:
        self.status = JobStatus.QUEUED
        self.queue_status = QueueStatus.QUEUED
        self.queue_position = queue_position
        self.queued_start_step = step
        self.queued_continue_pipeline = continue_pipeline
        self.stage = STEP_STAGE[step]
        self.progress = STEP_PROGRESS[step][0]
        self.error = None
        self._append_log(f"将从“{_step_label(step)}”开始执行")
        self.updated_at = utc_now()

    def set_queue_position(self, position: int) -> None:
        with self._lock:
            if self.queue_status != QueueStatus.QUEUED:
                return
            normalized = max(1, position)
            if self.queue_position == normalized:
                return
            self.queue_position = normalized
            self.updated_at = utc_now()
            self._persist()

    def mark_scheduler_running(self) -> None:
        with self._lock:
            self.queue_status = QueueStatus.RUNNING
            self.queue_position = None
            self.updated_at = utc_now()
            self._persist()

    def mark_waiting_for_input(self, step: JobStep) -> None:
        with self._lock:
            self.status = JobStatus.WAITING_FOR_INPUT
            self.queue_status = QueueStatus.WAITING_FOR_INPUT
            self.queue_position = None
            self.stage = JobStage.WAITING_FOR_INPUT
            self.current_step = None
            self.queued_start_step = step
            self.error = "运行时 API Key 已丢失，请重新输入后继续"
            self._append_log(self.error, level=LogLevel.WARNING)
            self.updated_at = utc_now()
            self._persist()

    def mark_interrupted(self) -> None:
        with self._lock:
            now = utc_now()
            step = self.current_step or self.queued_start_step
            if step is not None:
                state = self.steps[step]
                if state.status == StepStatus.RUNNING:
                    state.status = StepStatus.INTERRUPTED
                    state.error = "任务执行被应用退出中断"
                if state.attempts and state.attempts[-1].status == StepStatus.RUNNING:
                    attempt = state.attempts[-1]
                    attempt.status = StepStatus.INTERRUPTED
                    attempt.finished_at = now
                    attempt.error = "任务执行被应用退出中断"
            self.current_step = None
            self.queued_start_step = step
            self.status = JobStatus.INTERRUPTED
            self.queue_status = QueueStatus.INTERRUPTED
            self.queue_position = None
            self.stage = JobStage.INTERRUPTED
            self.interrupted_at = now
            self.error = "任务执行被应用退出中断，请从中断步骤重试"
            self._append_log(self.error, level=LogLevel.WARNING)
            self.updated_at = now
            self._persist()

    def mark_complete(self) -> None:
        with self._lock:
            self.status = JobStatus.COMPLETED
            self.queue_status = QueueStatus.COMPLETED
            self.queue_position = None
            self.stage = JobStage.COMPLETED
            self.progress = 100
            self.error = None
            self.current_step = None
            self._append_log("处理完成")
            self.updated_at = utc_now()
            self._persist()

    def mark_paused(self) -> None:
        with self._lock:
            next_step = next(
                (step for step in STEP_ORDER if self.steps[step].status != StepStatus.SUCCEEDED),
                None,
            )
            if next_step is None:
                self.mark_complete()
                return
            self.status = JobStatus.DRAFT
            self.queue_status = QueueStatus.DRAFT
            self.queue_position = None
            self.stage = STEP_STAGE[next_step]
            self.progress = STEP_PROGRESS[next_step][0]
            self.current_step = None
            self._append_log("步骤执行完成，任务已暂停")
            self.updated_at = utc_now()
            self._persist()

    def to_view(self) -> JobView:
        with self._lock:
            active = self.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            legacy_asr = isinstance(self.asr, LegacyASRSettings)
            views: list[JobStepView] = []
            for index, step in enumerate(STEP_ORDER):
                state = self.steps[step].model_copy(deep=True)
                state.config = self.config_payload(step)
                prerequisites_ok = all(
                    self.steps[item].status == StepStatus.SUCCEEDED
                    for item in STEP_ORDER[:index]
                )
                legacy_transcription = legacy_asr and step == JobStep.TRANSCRIPTION
                state.can_run = not active and prerequisites_ok and not legacy_transcription
                if legacy_transcription:
                    state.error = LEGACY_ASR_UNAVAILABLE_MESSAGE
                state.latest_duration_ms = (
                    state.attempts[-1].duration_ms if state.attempts else None
                )
                state.total_duration_ms = _total_attempt_duration(state.attempts)
                state.total_model_usage = merge_model_usage(
                    [
                        attempt.model_usage
                        for attempt in state.attempts
                        if attempt.model_usage is not None
                    ]
                )
                views.append(state)
            attempts = [attempt for state in views for attempt in state.attempts]
            return JobView(
                id=self.id,
                batch_id=self.batch_id,
                status=self.status,
                queue_status=self.queue_status,
                queue_position=self.queue_position,
                priority=self.priority,
                stage=self.stage,
                progress=self.progress,
                current_step=self.current_step,
                source_name=self.media.name,
                source_kind=self.media.source_kind,
                detected_language=self.detected_language,
                target_language=self.translation.target_language,
                asr_provider=self.asr.provider,
                translation_provider=self.translation.provider,
                created_at=self.created_at,
                updated_at=self.updated_at,
                interrupted_at=self.interrupted_at,
                subtitle_path=self.subtitle_path,
                error=self.error,
                logs=list(self.logs),
                steps=views,
                wall_duration_ms=_wall_duration_ms(
                    attempts,
                    status=self.status,
                    updated_at=self.updated_at,
                ),
                cumulative_attempt_duration_ms=_total_attempt_duration(attempts),
                total_model_usage=merge_model_usage(
                    [
                        attempt.model_usage
                        for attempt in attempts
                        if attempt.model_usage is not None
                    ]
                ),
            )

    def to_summary(self) -> JobSummaryView:
        with self._lock:
            attempts = [
                attempt
                for step in STEP_ORDER
                for attempt in self.steps[step].attempts
            ]
            summary_step = self.current_step
            if summary_step is None and self.status in {
                JobStatus.QUEUED,
                JobStatus.RUNNING,
                JobStatus.WAITING_FOR_INPUT,
                JobStatus.INTERRUPTED,
            }:
                summary_step = self.queued_start_step
            if summary_step is None and self.status != JobStatus.COMPLETED:
                summary_step = next(
                    (
                        step
                        for step in STEP_ORDER
                        if self.steps[step].status != StepStatus.SUCCEEDED
                    ),
                    None,
                )
            return JobSummaryView(
                id=self.id,
                batch_id=self.batch_id,
                source_name=self.media.name,
                source_kind=self.media.source_kind,
                status=self.status,
                queue_status=self.queue_status,
                current_step=summary_step,
                stage=self.stage,
                progress=self.progress,
                queue_position=self.queue_position,
                priority=self.priority,
                created_at=self.created_at,
                updated_at=self.updated_at,
                interrupted_at=self.interrupted_at,
                error=(self.error[:240] if self.error else None),
                subtitle_path=self.subtitle_path,
                wall_duration_ms=_wall_duration_ms(
                    attempts,
                    status=self.status,
                    updated_at=self.updated_at,
                ),
                cumulative_attempt_duration_ms=_total_attempt_duration(attempts),
                total_model_usage=merge_model_usage(
                    [
                        attempt.model_usage
                        for attempt in attempts
                        if attempt.model_usage is not None
                    ]
                ),
            )

    def to_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 3,
                "id": self.id,
                "batch_id": self.batch_id,
                "media": self.media.model_dump(mode="json"),
                "asr": self.asr.model_dump(mode="json"),
                "translation": self.translation.model_dump(mode="json"),
                "export": self.export.model_dump(mode="json"),
                "status": self.status.value,
                "queue_status": self.queue_status.value,
                "queue_position": self.queue_position,
                "priority": self.priority,
                "stage": self.stage.value,
                "progress": self.progress,
                "detected_language": self.detected_language,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "interrupted_at": (
                    self.interrupted_at.isoformat() if self.interrupted_at else None
                ),
                "error": self.error,
                "logs": [item.model_dump(mode="json") for item in self.logs],
                "steps": [
                    self.steps[step].model_dump(
                        mode="json",
                        exclude={
                            "config",
                            "can_run",
                            "latest_duration_ms",
                            "total_duration_ms",
                            "total_model_usage",
                        },
                    )
                    for step in STEP_ORDER
                ],
                "current_step": self.current_step.value if self.current_step else None,
                "queued_start_step": (
                    self.queued_start_step.value if self.queued_start_step else None
                ),
                "queued_continue_pipeline": self.queued_continue_pipeline,
            }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> JobRecord:
        raw_asr = payload["asr"]
        legacy_asr = (
            isinstance(raw_asr, Mapping)
            and raw_asr.get("provider") == "qwen3_asr"
        )
        if not legacy_asr and isinstance(raw_asr, Mapping):
            compatibility_defaults: dict[str, bool] = {}
            if "dynamic_chunking" not in raw_asr:
                # Jobs created before #17 used fixed windows.
                compatibility_defaults["dynamic_chunking"] = False
            if "selective_retry" not in raw_asr:
                # Jobs created before #16 only ran the first-pass decoder.
                compatibility_defaults["selective_retry"] = False
            if compatibility_defaults:
                # Preserve historical rerun semantics; new requests still default on.
                raw_asr = {**raw_asr, **compatibility_defaults}
        record = cls(
            id=str(payload["id"]),
            batch_id=(str(payload["batch_id"]) if payload.get("batch_id") else None),
            media=MediaStepSettings.model_validate(payload["media"]),
            asr=(
                LegacyASRSettings.model_validate(raw_asr)
                if legacy_asr
                else ASRSettings.model_validate(raw_asr)
            ),
            translation=TranslationStepSettings.model_validate(payload["translation"]),
            export=ExportSettings.model_validate(payload.get("export", {})),
            status=JobStatus(payload.get("status", JobStatus.DRAFT)),
            queue_status=QueueStatus(
                payload.get("queue_status", payload.get("status", QueueStatus.DRAFT))
            ),
            queue_position=(
                int(payload["queue_position"])
                if payload.get("queue_position") is not None
                else None
            ),
            priority=int(payload.get("priority", 0)),
            stage=JobStage(payload.get("stage", JobStage.DRAFT)),
            progress=int(payload.get("progress", 0)),
            detected_language=payload.get("detected_language"),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            interrupted_at=(
                datetime.fromisoformat(str(payload["interrupted_at"]))
                if payload.get("interrupted_at")
                else None
            ),
            error=payload.get("error"),
            logs=[LogEntry.model_validate(item) for item in payload.get("logs", [])],
            current_step=(
                JobStep(payload["current_step"])
                if payload.get("current_step")
                else None
            ),
            queued_start_step=(
                JobStep(payload["queued_start_step"])
                if payload.get("queued_start_step")
                else None
            ),
            queued_continue_pipeline=bool(
                payload.get("queued_continue_pipeline", True)
            ),
        )
        loaded_steps = {
            state.id: state
            for state in (
                JobStepView.model_validate(item) for item in payload.get("steps", [])
            )
        }
        record.steps = {
            step: loaded_steps.get(step, JobStepView(id=step, status=StepStatus.PENDING))
            for step in STEP_ORDER
        }
        return record


def _total_attempt_duration(attempts: list[StepAttemptView]) -> int | None:
    finished = [attempt for attempt in attempts if attempt.status != StepStatus.RUNNING]
    if not finished:
        return None
    if any(attempt.duration_ms is None for attempt in finished):
        return None
    return sum(attempt.duration_ms or 0 for attempt in finished)


def _wall_duration_ms(
    attempts: list[StepAttemptView],
    *,
    status: JobStatus,
    updated_at: datetime,
) -> int | None:
    if not attempts:
        return None
    started_at = min(attempt.started_at for attempt in attempts)
    if status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        finished_at = utc_now()
    elif status in {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
        JobStatus.WAITING_FOR_INPUT,
    }:
        finished_at = updated_at
    else:
        finished = [attempt.finished_at for attempt in attempts if attempt.finished_at]
        finished_at = max(finished) if finished else started_at
    return max(0, round((finished_at - started_at).total_seconds() * 1_000))


def _step_label(step: JobStep) -> str:
    return {
        JobStep.MEDIA: "媒体准备",
        JobStep.TRANSCRIPTION: "语音识别",
        JobStep.TRANSLATION: "字幕翻译",
        JobStep.EXPORT: "字幕导出",
    }[step]


class ProcessingPipeline:
    def __init__(
        self,
        job_root: Path,
        *,
        asr_factory: ASRFactory | None = None,
        model_manager: ModelManager | None = None,
        job_store: JobStore | None = None,
    ) -> None:
        self.store = job_store or JobStore(job_root)
        self.model_manager = model_manager
        self.asr_factory = asr_factory

    def _create_asr(self) -> ASRProvider:
        if self.asr_factory is not None:
            return self.asr_factory()
        return FasterWhisperProvider(self.model_manager)

    async def run_from(
        self,
        record: JobRecord,
        start_step: JobStep,
        *,
        api_key: str | None = None,
        continue_pipeline: bool = True,
    ) -> None:
        start_index = STEP_ORDER.index(start_step)
        selected = STEP_ORDER[start_index:] if continue_pipeline else (start_step,)
        for step in selected:
            await self.run_step(record, step, api_key=api_key)
        if all(
            record.steps[step].status == StepStatus.SUCCEEDED
            for step in STEP_ORDER
        ):
            record.mark_complete()
        else:
            record.mark_paused()

    async def run_step(
        self,
        record: JobRecord,
        step: JobStep,
        *,
        api_key: str | None = None,
    ) -> None:
        self._require_prerequisites(record, step)
        artifact = await self._run_step(record, step, api_key=api_key)
        completion_message = f"{_step_label(step)}完成"
        if (
            step == JobStep.TRANSCRIPTION
            and isinstance(record.asr, ASRSettings)
            and record.asr.hotwords
        ):
            completion_message += f"，已使用 {len(record.asr.hotwords)} 个提示词"
        record.complete_step(step, artifact, completion_message)

    def _require_prerequisites(self, record: JobRecord, step: JobStep) -> None:
        index = STEP_ORDER.index(step)
        missing = [
            item
            for item in STEP_ORDER[:index]
            if record.steps[item].status != StepStatus.SUCCEEDED
            or record.steps[item].artifact is None
        ]
        if missing:
            labels = "、".join(_step_label(item) for item in missing)
            raise ValueError(f"请先完成上游步骤：{labels}")

    async def _run_step(
        self,
        record: JobRecord,
        step: JobStep,
        *,
        api_key: str | None,
    ) -> StepArtifactView:
        if step == JobStep.MEDIA:
            return await self._prepare_media(record)
        if step == JobStep.TRANSCRIPTION:
            return await self._transcribe(record)
        if step == JobStep.TRANSLATION:
            return await self._translate(record, api_key=api_key)
        return await self._export(record)

    def _artifact(
        self,
        record: JobRecord,
        step: JobStep,
        *,
        path: Path,
        fingerprint: str,
        summary: Mapping[str, Any],
    ) -> StepArtifactView:
        attempt_number = record.steps[step].attempts[-1].number
        input_fingerprints = {
            item.value: artifact.fingerprint
            for item in STEP_ORDER[: STEP_ORDER.index(step)]
            if (artifact := record.steps[item].artifact) is not None
        }
        return StepArtifactView(
            id=f"{step.value}-{attempt_number}",
            step=step,
            path=str(path),
            fingerprint=fingerprint,
            config_fingerprint=_fingerprint(record.config_payload(step)),
            input_fingerprints=input_fingerprints,
            summary=dict(summary),
        )

    async def _prepare_media(self, record: JobRecord) -> StepArtifactView:
        record.begin_step(JobStep.MEDIA, "正在验证媒体文件")
        path = await _run_blocking_call(
            ensure_supported_video,
            Path(record.media.path),
        )
        stat = await _run_blocking_call(path.stat)
        payload = {
            "path": str(path),
            "name": path.name,
            "source_kind": record.media.source_kind.value,
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
        artifact_path, fingerprint = await _run_blocking_call(
            self.store.write_artifact,
            record.id,
            "media.json",
            payload,
        )
        record.update_step_progress(JobStep.MEDIA, 1.0)
        return self._artifact(
            record,
            JobStep.MEDIA,
            path=artifact_path,
            fingerprint=fingerprint,
            summary={"name": path.name, "size": stat.st_size},
        )

    async def _transcribe(self, record: JobRecord) -> StepArtifactView:
        if isinstance(record.asr, LegacyASRSettings):
            raise ValueError(LEGACY_ASR_UNAVAILABLE_MESSAGE)
        output_mode_label = (
            "逐词重排"
            if record.asr.output_mode == ASROutputMode.WORD_RESEGMENTED
            else "分片原始段"
        )
        chunking_label = (
            "VAD 动态分片" if record.asr.dynamic_chunking else "固定 60 秒分片"
        )
        retry_label = (
            "，低置信片段有界二次识别"
            if record.asr.selective_retry
            else "，仅单次识别"
        )
        timestamp_label = (
            "，实验性时间轴校正"
            if record.asr.timestamp_normalization
            else ""
        )
        hotword_label = (
            f"，已配置 {len(record.asr.hotwords)} 个提示词"
            if record.asr.hotwords
            else ""
        )
        message = (
            f"正在使用 Faster-Whisper {record.asr.model} 进行{chunking_label}识别"
            f"（{output_mode_label}{retry_label}{timestamp_label}{hotword_label}）"
        )
        record.begin_step(JobStep.TRANSCRIPTION, message)
        asr = self._create_asr()
        cancel_requested = threading.Event()

        def on_progress(value: float) -> None:
            if cancel_requested.is_set():
                raise _BlockingCallCancelled
            record.update_step_progress(JobStep.TRANSCRIPTION, value)

        transcription = await _run_blocking_call(
            asr.transcribe,
            record.source.path,
            language="auto",
            settings=record.asr,
            on_progress=on_progress,
            cancel_callback=cancel_requested.set,
        )
        payload = transcription.model_dump(mode="json")
        artifact_path, fingerprint = await _run_blocking_call(
            self.store.write_artifact,
            record.id,
            "transcription.json",
            payload,
        )
        record.set_detected_language(transcription.language)
        record.update_step_progress(JobStep.TRANSCRIPTION, 1.0)
        summary: dict[str, Any] = {
            "language": transcription.language,
            "segment_count": len(transcription.segments),
            "duration_seconds": transcription.duration_seconds,
            "hotword_count": len(record.asr.hotwords),
        }
        if transcription.diagnostics is not None:
            summary["diagnostics"] = {
                "schema_version": transcription.diagnostics.schema_version,
                "window_strategy": transcription.diagnostics.window_strategy,
                **transcription.diagnostics.summary.model_dump(mode="json"),
            }
        return self._artifact(
            record,
            JobStep.TRANSCRIPTION,
            path=artifact_path,
            fingerprint=fingerprint,
            summary=summary,
        )

    async def _translate(
        self,
        record: JobRecord,
        *,
        api_key: str | None,
    ) -> StepArtifactView:
        record.begin_step(
            JobStep.TRANSLATION,
            f"正在通过 {record.translation.provider.value} 翻译字幕",
        )
        transcription_artifact = record.steps[JobStep.TRANSCRIPTION].artifact
        if transcription_artifact is None:
            raise ValueError("缺少语音识别产物")
        transcription = TranscriptionResult.model_validate(
            self.store.read_artifact(Path(transcription_artifact.path))
        )
        if _language_key(transcription.language) == _language_key(
            record.translation.target_language
        ):
            raise ValueError("检测到的源语言与目标语言相同，请修改翻译步骤的目标语言")
        if (
            record.translation.provider == TranslationProviderName.DEEPSEEK
            and not (api_key or "").strip()
        ):
            raise ValueError("DeepSeek 需要 API Key，请在本次运行前填写")

        runtime_settings = record.translation.runtime_settings(
            SecretStr(api_key) if api_key else None
        )
        provider = create_translation_provider(runtime_settings)
        service = TranslationService(provider)
        source_items = segments_to_translation_items(transcription.segments)

        def on_progress(done: int, total: int) -> None:
            record.update_step_progress(
                JobStep.TRANSLATION,
                done / max(total, 1),
            )

        def on_recovery(message: str) -> None:
            record.update(message=message, level=LogLevel.WARNING)

        def on_usage(usage: ModelUsageSummary) -> None:
            record.record_model_usage(JobStep.TRANSLATION, usage)

        translated = await service.translate(
            source_items,
            source_language=transcription.language,
            target_language=record.translation.target_language,
            on_progress=on_progress,
            on_recovery=on_recovery,
            on_usage=on_usage,
        )
        payload = {
            "target_language": record.translation.target_language.value,
            "items": [item.model_dump(mode="json") for item in translated],
        }
        artifact_path, fingerprint = await _run_blocking_call(
            self.store.write_artifact,
            record.id,
            "translation.json",
            payload,
        )
        record.update_step_progress(JobStep.TRANSLATION, 1.0)
        return self._artifact(
            record,
            JobStep.TRANSLATION,
            path=artifact_path,
            fingerprint=fingerprint,
            summary={
                "target_language": record.translation.target_language.value,
                "item_count": len(translated),
            },
        )

    async def _export(self, record: JobRecord) -> StepArtifactView:
        record.begin_step(JobStep.EXPORT, "正在写入双语字幕")
        transcription_artifact = record.steps[JobStep.TRANSCRIPTION].artifact
        translation_artifact = record.steps[JobStep.TRANSLATION].artifact
        if transcription_artifact is None or translation_artifact is None:
            raise ValueError("缺少识别或翻译产物")
        transcription = TranscriptionResult.model_validate(
            self.store.read_artifact(Path(transcription_artifact.path))
        )
        translation_payload = self.store.read_artifact(Path(translation_artifact.path))
        translated = [
            TranslatedItem.model_validate(item)
            for item in translation_payload.get("items", [])
        ]

        subtitle_path = subtitle_output_path(record.source.path, record.export)
        validate_output_target(
            subtitle_path,
            overwrite_existing=record.export.overwrite_existing,
        )
        await _run_blocking_call(
            write_bilingual_srt,
            subtitle_path,
            transcription.segments,
            translated,
        )
        content = await _run_blocking_call(subtitle_path.read_bytes)
        fingerprint = hashlib.sha256(content).hexdigest()
        record.update_step_progress(JobStep.EXPORT, 1.0)
        return self._artifact(
            record,
            JobStep.EXPORT,
            path=subtitle_path,
            fingerprint=fingerprint,
            summary={
                "name": subtitle_path.name,
                "size": len(content),
                "target_language": record.translation.target_language.value,
            },
        )


class JobManager:
    def __init__(
        self,
        upload_store: UploadStore | None,
        pipeline: ProcessingPipeline,
        *,
        job_store: JobStore | None = None,
        scheduler_settings: SchedulerSettings | None = None,
    ) -> None:
        self.upload_store = upload_store
        self.pipeline = pipeline
        self.store = job_store or getattr(pipeline, "store", None)
        self._mutation_lock = threading.RLock()
        self._summary_snapshots: OrderedDict[str, _SummarySnapshot] = OrderedDict()
        self._summary_snapshot_item_count = 0
        self._summary_snapshot_clock: Callable[[], float] = time.monotonic
        self._summary_snapshot_ttl_seconds = SUMMARY_SNAPSHOT_TTL_SECONDS
        self._summary_snapshot_max_entries = SUMMARY_SNAPSHOT_MAX_ENTRIES
        self._summary_snapshot_max_items = SUMMARY_SNAPSHOT_MAX_ITEMS
        self.repository = JobRepository(self.store, JobRecord.from_payload)
        self._jobs = self.repository.records
        self.scheduler = JobScheduler(
            pipeline,
            self._record,
            step_order=STEP_ORDER,
            settings=scheduler_settings,
        )
        # Backward-compatible awaitable map for integrations that waited on `_tasks`.
        # Values are completion Futures, not one unbounded asyncio.Task per queued job.
        self._tasks = self.scheduler.completions
        self._recover_jobs()

    def _recover_jobs(self) -> None:
        def recovery_request(
            record: JobRecord,
        ) -> tuple[JobStep, tuple[JobStep, ...]]:
            start_step = record.current_step or record.queued_start_step or next(
                (
                    step
                    for step in STEP_ORDER
                    if record.steps[step].status != StepStatus.SUCCEEDED
                ),
                JobStep.MEDIA,
            )
            start_index = STEP_ORDER.index(start_step)
            selected = (
                STEP_ORDER[start_index:]
                if record.queued_continue_pipeline
                else (start_step,)
            )
            return start_step, tuple(selected)

        queued: list[JobRecord] = []
        for record in self.repository.list():
            if record.queue_status == QueueStatus.RUNNING or record.status == JobStatus.RUNNING:
                start_step, selected = recovery_request(record)
                record.mark_interrupted()
                if (
                    JobStep.TRANSLATION in selected
                    and record.translation.provider
                    == TranslationProviderName.DEEPSEEK
                ):
                    record.mark_waiting_for_input(start_step)
                continue
            if record.queue_status == QueueStatus.QUEUED or record.status == JobStatus.QUEUED:
                queued.append(record)

        queued.sort(
            key=lambda item: (
                item.queue_position if item.queue_position is not None else 2**31,
                -item.priority,
                item.created_at,
            )
        )
        for record in queued:
            start_step, selected = recovery_request(record)
            if (
                JobStep.TRANSLATION in selected
                and record.translation.provider == TranslationProviderName.DEEPSEEK
            ):
                record.mark_waiting_for_input(start_step)
                continue
            if JobStep.EXPORT in selected:
                try:
                    self.validate_output_claim(
                        record.media,
                        record.export,
                        exclude_job_id=record.id,
                    )
                except (OSError, ValueError) as exc:
                    record.fail_current(exc)
                    continue
            self.scheduler.restore(
                record.id,
                start_step,
                continue_pipeline=record.queued_continue_pipeline,
            )

    def start(self) -> None:
        self.scheduler.start()

    def resolve_source(self, request: JobCreateRequest) -> MediaStepSettings:
        if request.video_path:
            path = ensure_supported_video(Path(request.video_path)).resolve()
            return MediaStepSettings(
                source_kind=SourceKind.PATH,
                path=str(path),
                name=path.name,
            )
        if self.upload_store is None:
            raise ValueError("上传存储不可用")
        assert request.upload_id is not None
        item = self.upload_store.get(request.upload_id)
        path = ensure_supported_video(Path(item.path)).resolve()
        return MediaStepSettings(
            source_kind=SourceKind.UPLOAD,
            path=str(path),
            name=item.name,
        )

    @staticmethod
    def output_path(media: MediaStepSettings, export: ExportSettings) -> Path:
        return subtitle_output_path(Path(media.path), export)

    def output_claim_owner(
        self,
        path: Path,
        *,
        exclude_job_id: str | None = None,
    ) -> str | None:
        requested_key = output_path_key(path)
        with self._mutation_lock:
            for record in self.repository.list():
                if record.id == exclude_job_id:
                    continue
                try:
                    claimed_key = output_path_key(
                        self.output_path(record.media, record.export)
                    )
                except (OSError, ValueError):
                    continue
                if claimed_key == requested_key:
                    return record.id
        return None

    def validate_output_claim(
        self,
        media: MediaStepSettings,
        export: ExportSettings,
        *,
        exclude_job_id: str | None = None,
    ) -> Path:
        path = self.output_path(media, export)
        validate_output_target(path, overwrite_existing=export.overwrite_existing)
        owner = self.output_claim_owner(path, exclude_job_id=exclude_job_id)
        if owner is not None:
            raise ValueError(f"目标字幕路径已由任务 {owner} 占用")
        return path

    def create(
        self,
        request: JobCreateRequest,
        *,
        batch_id: str | None = None,
    ) -> JobView:
        if (
            request.auto_start
            and request.translation.provider == TranslationProviderName.DEEPSEEK
            and (
                request.translation.api_key is None
                or not request.translation.api_key.get_secret_value().strip()
            )
        ):
            raise ValueError("DeepSeek 自动启动需要本次运行 API Key")
        media = self.resolve_source(request)
        translation = TranslationStepSettings(
            target_language=request.target_language,
            provider=request.translation.provider,
            model=request.translation.model,
            endpoint=request.translation.endpoint,
            timeout_seconds=request.translation.timeout_seconds,
        )
        record = JobRecord(
            id=uuid.uuid4().hex,
            batch_id=batch_id,
            media=media,
            asr=request.asr.model_copy(deep=True),
            translation=translation,
            export=request.export.model_copy(deep=True),
        )
        record.update(message="任务草稿已创建")
        with self._mutation_lock:
            self.validate_output_claim(record.media, record.export)
            self.repository.add(record)
            if request.auto_start:
                try:
                    self._schedule(
                        record,
                        JobStep.MEDIA,
                        JobRunRequest(
                            api_key=request.translation.api_key,
                            continue_pipeline=True,
                        ),
                    )
                except Exception:
                    self.repository.delete(record.id)
                    raise
        return record.to_view()

    def update_step_config(
        self,
        job_id: str,
        step: JobStep,
        config: Mapping[str, Any],
    ) -> JobView:
        with self._mutation_lock:
            record = self._record(job_id)
            if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                raise ValueError("任务运行中，不能修改步骤配置")
            if step == JobStep.MEDIA:
                requested = MediaStepSettings.model_validate(config)
                path = ensure_supported_video(Path(requested.path)).resolve()
                media = requested.model_copy(update={"path": str(path), "name": path.name})
                self.validate_output_claim(
                    media,
                    record.export,
                    exclude_job_id=record.id,
                )
                record.media = media
            elif step == JobStep.TRANSCRIPTION:
                try:
                    record.asr = ASRSettings.model_validate(config)
                except ValidationError as exc:
                    # A manual config update bypasses FastAPI's request-model handler.
                    # Never echo rejected prompt text from Pydantic's default repr.
                    raise ValueError(_safe_validation_message(exc)) from exc
            elif step == JobStep.TRANSLATION:
                if "api_key" in config:
                    raise ValueError("API Key 只能随单次运行请求提交，不能写入任务配置")
                record.translation = TranslationStepSettings.model_validate(config)
            else:
                export = ExportSettings.model_validate(config)
                self.validate_output_claim(
                    record.media,
                    export,
                    exclude_job_id=record.id,
                )
                record.export = export
            record.steps[step].config_revision += 1
            record.invalidate_from(step, message=f"{_step_label(step)}配置已更新")
            return record.to_view()

    def run(self, job_id: str, request: JobRunRequest | None = None) -> JobView:
        with self._mutation_lock:
            record = self._record(job_id)
            start_step = next(
                (
                    step
                    for step in STEP_ORDER
                    if record.steps[step].status != StepStatus.SUCCEEDED
                ),
                JobStep.MEDIA,
            )
            return self._schedule(record, start_step, request or JobRunRequest())

    def run_step(
        self,
        job_id: str,
        step: JobStep,
        request: JobRunRequest | None = None,
    ) -> JobView:
        with self._mutation_lock:
            record = self._record(job_id)
            return self._schedule(record, step, request or JobRunRequest())

    def _schedule(
        self,
        record: JobRecord,
        start_step: JobStep,
        request: JobRunRequest,
    ) -> JobView:
        with self._mutation_lock:
            return self._schedule_locked(record, start_step, request)

    def _schedule_locked(
        self,
        record: JobRecord,
        start_step: JobStep,
        request: JobRunRequest,
    ) -> JobView:
        if self.scheduler.is_active(record.id) or record.status in {
            JobStatus.QUEUED,
            JobStatus.RUNNING,
        }:
            raise ValueError("任务正在运行")
        start_index = STEP_ORDER.index(start_step)
        selected = (
            STEP_ORDER[start_index:]
            if request.continue_pipeline
            else (start_step,)
        )
        if JobStep.TRANSCRIPTION in selected and isinstance(
            record.asr, LegacyASRSettings
        ):
            raise ValueError(LEGACY_ASR_UNAVAILABLE_MESSAGE)
        missing = [
            step
            for step in STEP_ORDER[:start_index]
            if record.steps[step].status != StepStatus.SUCCEEDED
            or record.steps[step].artifact is None
        ]
        if missing:
            labels = "、".join(_step_label(step) for step in missing)
            raise ValueError(f"请先完成上游步骤：{labels}")

        api_key = request.api_key.get_secret_value().strip() if request.api_key else None
        if (
            JobStep.TRANSLATION in selected
            and record.translation.provider == TranslationProviderName.DEEPSEEK
            and not api_key
        ):
            raise ValueError("DeepSeek 需要 API Key，请在本次运行前填写")

        if JobStep.EXPORT in selected:
            self.validate_output_claim(
                record.media,
                record.export,
                exclude_job_id=record.id,
            )

        self.scheduler.enqueue(
            record,
            start_step,
            api_key=api_key,
            continue_pipeline=request.continue_pipeline,
        )
        return record.to_view()

    def _record(self, job_id: str) -> JobRecord:
        return self.repository.get(job_id)

    def get(self, job_id: str) -> JobView:
        return self._record(job_id).to_view()

    def get_summary(self, job_id: str) -> JobSummaryView:
        return self._record(job_id).to_summary()

    def assign_batch(self, job_id: str, batch_id: str | None) -> JobSummaryView:
        with self._mutation_lock:
            record = self._record(job_id)
            record.assign_batch(batch_id)
            return record.to_summary()

    def list(self) -> list[JobView]:
        records = sorted(
            self.repository.list(),
            key=lambda item: item.created_at,
            reverse=True,
        )
        return [record.to_view() for record in records]

    def list_summaries(self) -> list[JobSummaryView]:
        records = sorted(
            self.repository.list(),
            key=lambda item: item.created_at,
            reverse=True,
        )
        return [record.to_summary() for record in records]

    def _drop_summary_snapshot_locked(self, snapshot_id: str) -> None:
        snapshot = self._summary_snapshots.pop(snapshot_id, None)
        if snapshot is not None:
            self._summary_snapshot_item_count -= len(snapshot.items)

    def _purge_summary_snapshots_locked(self, now: float) -> None:
        expired = [
            snapshot_id
            for snapshot_id, snapshot in self._summary_snapshots.items()
            if snapshot.expires_at_monotonic <= now
        ]
        for snapshot_id in expired:
            self._drop_summary_snapshot_locked(snapshot_id)

    def _create_summary_snapshot_locked(
        self,
        items: list[JobSummaryView],
        *,
        server_time: datetime,
        statuses: tuple[JobStatus, ...] | None,
        batch_id: str | None,
        query: str | None,
        updated_after: datetime | None,
    ) -> _SummarySnapshot:
        max_items = max(1, self._summary_snapshot_max_items)
        if len(items) > max_items:
            raise ValueError("分页结果超过快照上限，请增加筛选条件后重试")

        now = self._summary_snapshot_clock()
        self._purge_summary_snapshots_locked(now)
        max_entries = max(1, self._summary_snapshot_max_entries)
        while self._summary_snapshots and (
            len(self._summary_snapshots) >= max_entries
            or self._summary_snapshot_item_count + len(items) > max_items
        ):
            oldest_id = next(iter(self._summary_snapshots))
            self._drop_summary_snapshot_locked(oldest_id)

        filters = _summary_filter_payload(
            statuses=statuses,
            batch_id=batch_id,
            query=query,
            updated_after=updated_after,
        )
        snapshot_id = uuid.uuid4().hex
        while snapshot_id in self._summary_snapshots:
            snapshot_id = uuid.uuid4().hex
        snapshot = _SummarySnapshot(
            snapshot_id=snapshot_id,
            filter_fingerprint=_fingerprint(filters),
            server_time=server_time,
            statuses=statuses,
            batch_id=batch_id,
            query=query,
            updated_after=updated_after,
            items=tuple(item.model_copy(deep=True) for item in items),
            expires_at_monotonic=now
            + max(1.0, self._summary_snapshot_ttl_seconds),
        )
        self._summary_snapshots[snapshot_id] = snapshot
        self._summary_snapshot_item_count += len(snapshot.items)
        return snapshot

    def _get_summary_snapshot_locked(
        self,
        cursor: _SummaryCursor,
    ) -> _SummarySnapshot:
        self._purge_summary_snapshots_locked(self._summary_snapshot_clock())
        snapshot = self._summary_snapshots.get(cursor.snapshot_id)
        if snapshot is None:
            raise ValueError("分页 cursor 已过期或已被淘汰，请重新查询")
        if snapshot.filter_fingerprint != cursor.filter_fingerprint:
            raise ValueError("分页 cursor 无效")
        if cursor.offset >= len(snapshot.items):
            raise ValueError("分页 cursor 无效")
        self._summary_snapshots.move_to_end(snapshot.snapshot_id)
        return snapshot

    def list_summary_page(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        statuses: set[JobStatus] | None = None,
        batch_id: str | None = None,
        query: str | None = None,
        updated_after: datetime | None = None,
    ) -> JobSummaryPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1 到 200 之间")
        provided_statuses = (
            tuple(sorted(statuses, key=lambda status: status.value))
            if statuses is not None
            else None
        )
        provided_batch_id = (
            (batch_id.strip() or None) if batch_id is not None else None
        )
        provided_query = (
            (query.strip().casefold() or None) if query is not None else None
        )
        provided_updated_after = (
            _normalized_datetime(updated_after) if updated_after is not None else None
        )

        with self._mutation_lock:
            cursor_state = _decode_summary_cursor(cursor) if cursor is not None else None
            if cursor_state is not None:
                snapshot = self._get_summary_snapshot_locked(cursor_state)
                mismatched = (
                    (statuses is not None and provided_statuses != snapshot.statuses)
                    or (
                        batch_id is not None
                        and provided_batch_id != snapshot.batch_id
                    )
                    or (query is not None and provided_query != snapshot.query)
                    or (
                        updated_after is not None
                        and provided_updated_after != snapshot.updated_after
                    )
                )
                if mismatched:
                    raise ValueError("分页 cursor 与筛选条件不匹配")
                end = min(cursor_state.offset + limit, len(snapshot.items))
                items = [
                    item.model_copy(deep=True)
                    for item in snapshot.items[cursor_state.offset : end]
                ]
                has_more = end < len(snapshot.items)
                return JobSummaryPage(
                    items=items,
                    next_cursor=(
                        _encode_summary_cursor(snapshot, end) if has_more else None
                    ),
                    has_more=has_more,
                    total=len(snapshot.items),
                    server_time=snapshot.server_time,
                )

            effective_statuses = provided_statuses
            effective_batch_id = provided_batch_id
            effective_query = provided_query
            effective_updated_after = provided_updated_after
            server_time = utc_now() - timedelta(microseconds=1)

            summaries = [record.to_summary() for record in self.repository.list()]
            filtered = [
                summary
                for summary in summaries
                if summary.created_at <= server_time
                and (
                    effective_statuses is None
                    or summary.status in effective_statuses
                )
                and (
                    effective_batch_id is None
                    or summary.batch_id == effective_batch_id
                )
                and (
                    effective_query is None
                    or effective_query in summary.source_name.casefold()
                )
                and (
                    effective_updated_after is None
                    or (
                        summary.updated_at > effective_updated_after
                        and summary.updated_at <= server_time
                    )
                )
            ]
            filtered.sort(
                key=lambda summary: (summary.created_at, summary.id),
                reverse=True,
            )
            total = len(filtered)
            items = filtered[:limit]
            has_more = total > limit
            snapshot = (
                self._create_summary_snapshot_locked(
                    filtered,
                    server_time=server_time,
                    statuses=effective_statuses,
                    batch_id=effective_batch_id,
                    query=effective_query,
                    updated_after=effective_updated_after,
                )
                if has_more
                else None
            )
            return JobSummaryPage(
                items=items,
                next_cursor=(
                    _encode_summary_cursor(snapshot, len(items))
                    if snapshot is not None
                    else None
                ),
                has_more=has_more,
                total=total,
                server_time=server_time,
            )

    def cancel(self, job_id: str) -> JobView:
        record = self._record(job_id)
        if not self.scheduler.cancel(job_id):
            raise ValueError("任务不在队列或运行中")
        return record.to_view()

    async def wait(self, job_id: str) -> None:
        self._record(job_id)
        await self.scheduler.wait(job_id)

    def delete(self, job_id: str) -> None:
        with self._mutation_lock:
            record = self._record(job_id)
            if self.scheduler.is_active(record.id) or record.status in {
                JobStatus.QUEUED,
                JobStatus.RUNNING,
            }:
                raise ValueError("任务运行中，不能删除")
            self.repository.delete(job_id)

    async def shutdown(self) -> None:
        await self.scheduler.shutdown()
        with self._mutation_lock:
            self._summary_snapshots.clear()
            self._summary_snapshot_item_count = 0
