from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr

from .asr import ASRProvider, FasterWhisperProvider, Qwen3ASRProvider
from .asr.base import TranscriptionResult
from .job_store import JobStore
from .media import ensure_supported_video
from .model_manager import ModelManager
from .models import (
    ASROutputMode,
    ASRProviderName,
    ASRSettings,
    ExportSettings,
    JobCreateRequest,
    JobRunRequest,
    JobStage,
    JobStatus,
    JobStep,
    JobStepView,
    JobView,
    LogEntry,
    LogLevel,
    MediaStepSettings,
    ResolvedSource,
    SourceKind,
    StepArtifactView,
    StepAttemptView,
    StepStatus,
    TargetLanguage,
    TranslatedItem,
    TranslationProviderName,
    TranslationStepSettings,
    utc_now,
)
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


def _fingerprint(payload: Mapping[str, Any]) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _default_steps() -> dict[JobStep, JobStepView]:
    return {
        step: JobStepView(id=step, status=StepStatus.PENDING)
        for step in STEP_ORDER
    }


@dataclass
class JobRecord:
    id: str
    media: MediaStepSettings
    asr: ASRSettings = field(default_factory=ASRSettings)
    translation: TranslationStepSettings = field(default_factory=TranslationStepSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    status: JobStatus = JobStatus.DRAFT
    stage: JobStage = JobStage.DRAFT
    progress: int = 0
    detected_language: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    error: str | None = None
    logs: list[LogEntry] = field(default_factory=list)
    steps: dict[JobStep, JobStepView] = field(default_factory=_default_steps)
    current_step: JobStep | None = None
    _persist_callback: PersistCallback | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

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
            attempt = StepAttemptView(
                number=len(state.attempts) + 1,
                status=StepStatus.RUNNING,
                config=self.config_payload(step),
            )
            state.attempts.append(attempt)
            state.status = StepStatus.RUNNING
            state.progress = 0
            state.error = None
            self.current_step = step
            self.status = JobStatus.RUNNING
            self.stage = STEP_STAGE[step]
            self.error = None
            self.progress = STEP_PROGRESS[step][0]
            self._append_log(message)
            self.updated_at = utc_now()
            self._persist()

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
            attempt.finished_at = utc_now()
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
                attempt.finished_at = utc_now()
                attempt.error = message
            self.current_step = None
            self.status = JobStatus.FAILED
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
                    attempt.finished_at = utc_now()
                    attempt.error = "任务已取消"
            self.current_step = None
            self.status = JobStatus.CANCELLED
            self.stage = JobStage.CANCELLED
            self.error = "任务已取消"
            self._append_log("任务已取消", level=LogLevel.WARNING)
            self.updated_at = utc_now()
            self._persist()

    def invalidate_from(self, step: JobStep, *, message: str | None = None) -> None:
        with self._lock:
            if self.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                raise ValueError("任务运行中，不能修改配置或重置步骤")
            start_index = STEP_ORDER.index(step)
            for item in STEP_ORDER[start_index:]:
                state = self.steps[item]
                state.status = StepStatus.STALE if state.artifact else StepStatus.PENDING
                state.progress = 0
                state.error = None
            self.status = JobStatus.DRAFT
            self.stage = STEP_STAGE[step]
            self.progress = STEP_PROGRESS[step][0]
            self.error = None
            self.current_step = None
            if message:
                self._append_log(message)
            self.updated_at = utc_now()
            self._persist()

    def mark_queued(self, step: JobStep) -> None:
        with self._lock:
            self.status = JobStatus.QUEUED
            self.stage = STEP_STAGE[step]
            self.progress = STEP_PROGRESS[step][0]
            self.error = None
            self._append_log(f"将从“{_step_label(step)}”开始执行")
            self.updated_at = utc_now()
            self._persist()

    def mark_complete(self) -> None:
        with self._lock:
            self.status = JobStatus.COMPLETED
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
            self.stage = STEP_STAGE[next_step]
            self.progress = STEP_PROGRESS[next_step][0]
            self.current_step = None
            self._append_log("步骤执行完成，任务已暂停")
            self.updated_at = utc_now()
            self._persist()

    def to_view(self) -> JobView:
        with self._lock:
            active = self.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            views: list[JobStepView] = []
            for index, step in enumerate(STEP_ORDER):
                state = self.steps[step].model_copy(deep=True)
                state.config = self.config_payload(step)
                prerequisites_ok = all(
                    self.steps[item].status == StepStatus.SUCCEEDED
                    for item in STEP_ORDER[:index]
                )
                state.can_run = not active and prerequisites_ok
                views.append(state)
            return JobView(
                id=self.id,
                status=self.status,
                stage=self.stage,
                progress=self.progress,
                source_name=self.media.name,
                source_kind=self.media.source_kind,
                detected_language=self.detected_language,
                target_language=self.translation.target_language,
                asr_provider=self.asr.provider,
                translation_provider=self.translation.provider,
                created_at=self.created_at,
                updated_at=self.updated_at,
                subtitle_path=self.subtitle_path,
                error=self.error,
                logs=list(self.logs),
                steps=views,
            )

    def to_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "id": self.id,
                "media": self.media.model_dump(mode="json"),
                "asr": self.asr.model_dump(mode="json"),
                "translation": self.translation.model_dump(mode="json"),
                "export": self.export.model_dump(mode="json"),
                "status": self.status.value,
                "stage": self.stage.value,
                "progress": self.progress,
                "detected_language": self.detected_language,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "error": self.error,
                "logs": [item.model_dump(mode="json") for item in self.logs],
                "steps": [
                    self.steps[step].model_dump(
                        mode="json", exclude={"config", "can_run"}
                    )
                    for step in STEP_ORDER
                ],
                "current_step": self.current_step.value if self.current_step else None,
            }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> JobRecord:
        record = cls(
            id=str(payload["id"]),
            media=MediaStepSettings.model_validate(payload["media"]),
            asr=ASRSettings.model_validate(payload["asr"]),
            translation=TranslationStepSettings.model_validate(payload["translation"]),
            export=ExportSettings.model_validate(payload.get("export", {})),
            status=JobStatus(payload.get("status", JobStatus.DRAFT)),
            stage=JobStage(payload.get("stage", JobStage.DRAFT)),
            progress=int(payload.get("progress", 0)),
            detected_language=payload.get("detected_language"),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            error=payload.get("error"),
            logs=[LogEntry.model_validate(item) for item in payload.get("logs", [])],
            current_step=(
                JobStep(payload["current_step"])
                if payload.get("current_step")
                else None
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

    def _create_asr(self, provider: ASRProviderName) -> ASRProvider:
        if self.asr_factory is not None:
            return self.asr_factory()
        if provider == ASRProviderName.QWEN3_ASR:
            if self.model_manager is None:
                raise RuntimeError("Qwen3-ASR 需要应用模型管理器")
            return Qwen3ASRProvider(self.model_manager)
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
            self._require_prerequisites(record, step)
            artifact = await self._run_step(record, step, api_key=api_key)
            record.complete_step(step, artifact, f"{_step_label(step)}完成")
        if all(
            record.steps[step].status == StepStatus.SUCCEEDED
            for step in STEP_ORDER
        ):
            record.mark_complete()
        else:
            record.mark_paused()

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
        path = await asyncio.to_thread(ensure_supported_video, Path(record.media.path))
        stat = await asyncio.to_thread(path.stat)
        payload = {
            "path": str(path),
            "name": path.name,
            "source_kind": record.media.source_kind.value,
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
        artifact_path, fingerprint = await asyncio.to_thread(
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
        output_mode_label = (
            "逐词重排"
            if record.asr.output_mode == ASROutputMode.WORD_RESEGMENTED
            else "分片原始段"
        )
        message = (
            f"正在使用 Qwen3-ASR {record.asr.model} 识别并生成精确时间戳"
            if record.asr.provider == ASRProviderName.QWEN3_ASR
            else (
                f"正在使用 Faster-Whisper {record.asr.model} 进行 60 秒分片识别"
                f"（{output_mode_label}）"
            )
        )
        record.begin_step(JobStep.TRANSCRIPTION, message)
        asr = self._create_asr(record.asr.provider)

        def on_progress(value: float) -> None:
            record.update_step_progress(JobStep.TRANSCRIPTION, value)

        transcription = await asyncio.to_thread(
            asr.transcribe,
            record.source.path,
            language="auto",
            settings=record.asr,
            on_progress=on_progress,
        )
        payload = transcription.model_dump(mode="json")
        artifact_path, fingerprint = await asyncio.to_thread(
            self.store.write_artifact,
            record.id,
            "transcription.json",
            payload,
        )
        record.set_detected_language(transcription.language)
        record.update_step_progress(JobStep.TRANSCRIPTION, 1.0)
        return self._artifact(
            record,
            JobStep.TRANSCRIPTION,
            path=artifact_path,
            fingerprint=fingerprint,
            summary={
                "language": transcription.language,
                "segment_count": len(transcription.segments),
                "duration_seconds": transcription.duration_seconds,
            },
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

        translated = await service.translate(
            source_items,
            source_language=transcription.language,
            target_language=record.translation.target_language,
            on_progress=on_progress,
            on_recovery=on_recovery,
        )
        payload = {
            "target_language": record.translation.target_language.value,
            "items": [item.model_dump(mode="json") for item in translated],
        }
        artifact_path, fingerprint = await asyncio.to_thread(
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

        output_directory = (
            Path(record.export.output_directory).expanduser().resolve()
            if record.export.output_directory
            else record.source.path.parent
        )
        subtitle_path = output_directory / record.source.path.with_suffix(".srt").name
        if subtitle_path.exists() and not record.export.overwrite_existing:
            raise FileExistsError("目标字幕已存在，请允许覆盖或修改输出目录")
        await asyncio.to_thread(
            write_bilingual_srt,
            subtitle_path,
            transcription.segments,
            translated,
        )
        content = await asyncio.to_thread(subtitle_path.read_bytes)
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
    ) -> None:
        self.upload_store = upload_store
        self.pipeline = pipeline
        self.store = job_store or getattr(pipeline, "store", None)
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._load_jobs()

    def _load_jobs(self) -> None:
        if self.store is None:
            return
        for payload in self.store.load_jobs():
            try:
                record = JobRecord.from_payload(payload)
            except (KeyError, TypeError, ValueError):
                continue
            self._attach(record)
            self._jobs[record.id] = record
            if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                record.fail_current(RuntimeError("应用在任务运行期间退出，请从当前步骤重试"))

    def _attach(self, record: JobRecord) -> None:
        if self.store is None:
            record.attach_persistence(None)
            return
        record.attach_persistence(
            lambda payload, job_id=record.id: self.store.save_job(job_id, payload)
        )

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

    def create(self, request: JobCreateRequest) -> JobView:
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
            media=media,
            asr=request.asr.model_copy(deep=True),
            translation=translation,
            export=request.export.model_copy(deep=True),
        )
        record.update(message="任务草稿已创建")
        self._attach(record)
        self._jobs[record.id] = record
        record.update()
        if request.auto_start:
            self.run(
                record.id,
                JobRunRequest(
                    api_key=request.translation.api_key,
                    continue_pipeline=True,
                ),
            )
        return record.to_view()

    def update_step_config(
        self,
        job_id: str,
        step: JobStep,
        config: Mapping[str, Any],
    ) -> JobView:
        record = self._record(job_id)
        if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise ValueError("任务运行中，不能修改步骤配置")
        if step == JobStep.MEDIA:
            requested = MediaStepSettings.model_validate(config)
            path = ensure_supported_video(Path(requested.path)).resolve()
            record.media = requested.model_copy(update={"path": str(path), "name": path.name})
        elif step == JobStep.TRANSCRIPTION:
            record.asr = ASRSettings.model_validate(config)
        elif step == JobStep.TRANSLATION:
            if "api_key" in config:
                raise ValueError("API Key 只能随单次运行请求提交，不能写入任务配置")
            record.translation = TranslationStepSettings.model_validate(config)
        else:
            record.export = ExportSettings.model_validate(config)
        record.steps[step].config_revision += 1
        record.invalidate_from(step, message=f"{_step_label(step)}配置已更新")
        return record.to_view()

    def run(self, job_id: str, request: JobRunRequest | None = None) -> JobView:
        record = self._record(job_id)
        start_step = next(
            (step for step in STEP_ORDER if record.steps[step].status != StepStatus.SUCCEEDED),
            JobStep.MEDIA,
        )
        return self._schedule(record, start_step, request or JobRunRequest())

    def run_step(
        self,
        job_id: str,
        step: JobStep,
        request: JobRunRequest | None = None,
    ) -> JobView:
        record = self._record(job_id)
        return self._schedule(record, step, request or JobRunRequest())

    def _schedule(
        self,
        record: JobRecord,
        start_step: JobStep,
        request: JobRunRequest,
    ) -> JobView:
        if record.id in self._tasks or record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise ValueError("任务正在运行")
        start_index = STEP_ORDER.index(start_step)
        missing = [
            step
            for step in STEP_ORDER[:start_index]
            if record.steps[step].status != StepStatus.SUCCEEDED
            or record.steps[step].artifact is None
        ]
        if missing:
            labels = "、".join(_step_label(step) for step in missing)
            raise ValueError(f"请先完成上游步骤：{labels}")

        record.invalidate_from(start_step)
        selected = (
            STEP_ORDER[start_index:]
            if request.continue_pipeline
            else (start_step,)
        )
        api_key = request.api_key.get_secret_value().strip() if request.api_key else None
        if (
            JobStep.TRANSLATION in selected
            and record.translation.provider == TranslationProviderName.DEEPSEEK
            and not api_key
        ):
            raise ValueError("DeepSeek 需要 API Key，请在本次运行前填写")

        record.mark_queued(start_step)
        task = asyncio.create_task(
            self._run(
                record,
                start_step,
                api_key=api_key,
                continue_pipeline=request.continue_pipeline,
            ),
            name=f"captionnest-job-{record.id}",
        )
        self._tasks[record.id] = task
        task.add_done_callback(
            lambda completed, job_id=record.id: self._task_done(job_id, completed)
        )
        return record.to_view()

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)

    async def _run(
        self,
        record: JobRecord,
        start_step: JobStep,
        *,
        api_key: str | None,
        continue_pipeline: bool,
    ) -> None:
        try:
            await self.pipeline.run_from(
                record,
                start_step,
                api_key=api_key,
                continue_pipeline=continue_pipeline,
            )
        except asyncio.CancelledError:
            record.cancel_current()
            raise
        except Exception as exc:
            record.fail_current(exc, secrets=(api_key or "",))

    def _record(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError("任务不存在") from exc

    def get(self, job_id: str) -> JobView:
        return self._record(job_id).to_view()

    def list(self) -> list[JobView]:
        records = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
        return [record.to_view() for record in records]

    def delete(self, job_id: str) -> None:
        record = self._record(job_id)
        if record.id in self._tasks or record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise ValueError("任务运行中，不能删除")
        self._jobs.pop(job_id, None)
        if self.store is not None:
            self.store.delete_job(job_id)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
