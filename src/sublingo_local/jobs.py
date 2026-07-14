from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .asr import ASRProvider, FasterWhisperProvider
from .media import ensure_supported_video
from .model_manager import ModelManager
from .models import (
    JobCreateRequest,
    JobStage,
    JobStatus,
    JobView,
    LogEntry,
    LogLevel,
    ResolvedSource,
    SourceKind,
    TargetLanguage,
    utc_now,
)
from .storage import UploadStore
from .subtitles import segments_to_translation_items, write_bilingual_srt
from .translation import TranslationService, create_translation_provider

ASRFactory = Callable[[], ASRProvider]


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


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result


@dataclass
class JobRecord:
    id: str
    request: JobCreateRequest
    source: ResolvedSource
    status: JobStatus = JobStatus.QUEUED
    stage: JobStage = JobStage.QUEUED
    progress: int = 0
    detected_language: str | None = None
    created_at: object = field(default_factory=utc_now)
    updated_at: object = field(default_factory=utc_now)
    subtitle_path: str | None = None
    error: str | None = None
    logs: list[LogEntry] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def secrets(self) -> tuple[str, ...]:
        key = self.request.translation.api_key
        return (key.get_secret_value(),) if key else ()

    def update(
        self,
        *,
        status: JobStatus | None = None,
        stage: JobStage | None = None,
        progress: int | None = None,
        message: str | None = None,
        level: LogLevel = LogLevel.INFO,
    ) -> None:
        with self._lock:
            if status is not None:
                self.status = status
            if stage is not None:
                self.stage = stage
            if progress is not None:
                self.progress = max(0, min(100, progress))
            if message:
                self.logs.append(LogEntry(level=level, message=_redact(message, self.secrets)))
                self.logs = self.logs[-200:]
            self.updated_at = utc_now()

    def fail(self, exc: Exception) -> None:
        message = _redact(str(exc) or type(exc).__name__, self.secrets)
        with self._lock:
            self.status = JobStatus.FAILED
            self.stage = JobStage.FAILED
            self.error = message
            self.logs.append(LogEntry(level=LogLevel.ERROR, message=message))
            self.logs = self.logs[-200:]
            self.updated_at = utc_now()

    def clear_api_key(self) -> None:
        with self._lock:
            self.request.translation.api_key = None

    def to_view(self) -> JobView:
        with self._lock:
            return JobView(
                id=self.id,
                status=self.status,
                stage=self.stage,
                progress=self.progress,
                source_name=self.source.name,
                source_kind=self.source.kind,
                detected_language=self.detected_language,
                target_language=self.request.target_language,
                translation_provider=self.request.translation.provider,
                created_at=self.created_at,
                updated_at=self.updated_at,
                subtitle_path=self.subtitle_path,
                error=self.error,
                logs=list(self.logs),
            )


class ProcessingPipeline:
    def __init__(
        self,
        temp_root: Path,
        *,
        asr_factory: ASRFactory | None = None,
        model_manager: ModelManager | None = None,
    ) -> None:
        self.temp_root = temp_root
        self.temp_root.mkdir(parents=True, exist_ok=True)
        if asr_factory is not None:
            self.asr_factory = asr_factory
        elif model_manager is not None:
            self.asr_factory = lambda: FasterWhisperProvider(model_manager)
        else:
            self.asr_factory = FasterWhisperProvider

    async def run(self, record: JobRecord) -> None:
        record.update(
            status=JobStatus.RUNNING,
            stage=JobStage.EXTRACTING,
            progress=3,
            message="正在准备媒体文件（内置解码器，无需 FFmpeg）",
        )
        record.update(
            stage=JobStage.TRANSCRIBING,
            progress=12,
            message=f"正在使用 Faster-Whisper {record.request.asr.model} 识别",
        )

        asr = self.asr_factory()

        def on_asr_progress(value: float) -> None:
            record.update(progress=12 + round(max(0.0, min(1.0, value)) * 48))

        transcription = await asyncio.to_thread(
            asr.transcribe,
            record.source.path,
            language="auto",
            settings=record.request.asr,
            on_progress=on_asr_progress,
        )

        record.detected_language = transcription.language
        record.update(message=f"识别完成：{len(transcription.segments)} 条字幕")
        if _language_key(transcription.language) == _language_key(record.request.target_language):
            raise ValueError("检测到的源语言与目标语言相同，请选择其他目标语言")

        record.update(
            stage=JobStage.TRANSLATING,
            progress=62,
            message=f"正在通过 {record.request.translation.provider.value} 翻译",
        )
        provider = create_translation_provider(record.request.translation)
        service = TranslationService(provider)
        source_items = segments_to_translation_items(transcription.segments)

        def on_translation_progress(done: int, total: int) -> None:
            record.update(progress=62 + round(done / max(total, 1) * 30))

        translated = await service.translate(
            source_items,
            source_language=transcription.language,
            target_language=record.request.target_language,
            on_progress=on_translation_progress,
        )
        record.update(stage=JobStage.WRITING, progress=95, message="正在写入双语字幕")
        subtitle_path = record.source.path.with_suffix(".srt")
        await asyncio.to_thread(
            write_bilingual_srt,
            subtitle_path,
            transcription.segments,
            translated,
        )
        record.subtitle_path = str(subtitle_path)
        record.update(
            status=JobStatus.COMPLETED,
            stage=JobStage.COMPLETED,
            progress=100,
            message="处理完成",
        )


class JobManager:
    def __init__(self, upload_store: UploadStore, pipeline: ProcessingPipeline) -> None:
        self.upload_store = upload_store
        self.pipeline = pipeline
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def resolve_source(self, request: JobCreateRequest) -> ResolvedSource:
        if request.video_path:
            path = ensure_supported_video(Path(request.video_path))
            return ResolvedSource(kind=SourceKind.PATH, path=path, name=path.name)
        assert request.upload_id is not None
        item = self.upload_store.get(request.upload_id)
        path = ensure_supported_video(Path(item.path))
        return ResolvedSource(kind=SourceKind.UPLOAD, path=path, name=item.name)

    def create(self, request: JobCreateRequest) -> JobView:
        source = self.resolve_source(request)
        record = JobRecord(id=uuid.uuid4().hex, request=request, source=source)
        record.update(message="任务已加入队列")
        self._jobs[record.id] = record
        task = asyncio.create_task(self._run(record), name=f"captionnest-job-{record.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record.to_view()

    async def _run(self, record: JobRecord) -> None:
        try:
            await self.pipeline.run(record)
        except asyncio.CancelledError:
            record.update(
                status=JobStatus.CANCELLED,
                stage=JobStage.CANCELLED,
                message="任务已取消",
                level=LogLevel.WARNING,
            )
            raise
        except Exception as exc:
            record.fail(exc)
        finally:
            record.clear_api_key()

    def get(self, job_id: str) -> JobView:
        try:
            return self._jobs[job_id].to_view()
        except KeyError as exc:
            raise KeyError("任务不存在") from exc

    def list(self) -> list[JobView]:
        return [record.to_view() for record in reversed(self._jobs.values())]

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
