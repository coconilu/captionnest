from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class TranslationProviderName(StrEnum):
    CODEX_SPARK = "codex_spark"
    LM_STUDIO = "lmstudio"
    DEEPSEEK = "deepseek"


class ASRProviderName(StrEnum):
    FASTER_WHISPER = "faster_whisper"


class ASRModelName(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_V3_TURBO = "large-v3-turbo"
    LARGE_V3 = "large-v3"


class ASROutputMode(StrEnum):
    CHUNK_SEGMENTS = "chunk_segments"
    WORD_RESEGMENTED = "word_resegmented"


ASR_HOTWORD_MAX_ENTRIES = 50
ASR_HOTWORD_MAX_ENTRY_CHARACTERS = 64
ASR_HOTWORD_MAX_TOTAL_CHARACTERS = 512


class TargetLanguage(StrEnum):
    ZH_CN = "zh-CN"
    EN = "en"
    KO = "ko"


class SourceKind(StrEnum):
    PATH = "path"
    UPLOAD = "upload"


class JobStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStep(StrEnum):
    MEDIA = "media"
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation"
    EXPORT = "export"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"


class LogLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ASRSettings(BaseModel):
    provider: ASRProviderName = ASRProviderName.FASTER_WHISPER
    model: ASRModelName = ASRModelName.SMALL
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    vad_filter: bool = True
    dynamic_chunking: bool = True
    selective_retry: bool = True
    beam_size: int = Field(default=5, ge=1, le=20)
    output_mode: ASROutputMode = ASROutputMode.WORD_RESEGMENTED
    hotwords: list[str] = Field(
        default_factory=list,
        max_length=ASR_HOTWORD_MAX_ENTRIES,
        json_schema_extra={
            "max_item_characters": ASR_HOTWORD_MAX_ENTRY_CHARACTERS,
            "max_total_characters": ASR_HOTWORD_MAX_TOTAL_CHARACTERS,
        },
    )

    @field_validator("model", mode="before")
    @classmethod
    def strip_model(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("compute_type")
    @classmethod
    def non_empty_compute_type(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("hotwords", mode="before")
    @classmethod
    def normalize_hotwords(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("提示词必须是字符串数组")

        normalized: list[str] = []
        seen: set[str] = set()
        total_characters = 0
        for index, raw_item in enumerate(value, start=1):
            if not isinstance(raw_item, str):
                raise ValueError(f"第 {index} 个提示词必须是文本")
            item = raw_item.strip()
            if not item:
                continue
            if any(
                ord(character) < 32
                or ord(character) == 127
                or character in {"\u2028", "\u2029"}
                for character in item
            ):
                raise ValueError(f"第 {index} 个提示词不能包含换行或控制字符")
            if len(item) > ASR_HOTWORD_MAX_ENTRY_CHARACTERS:
                raise ValueError(
                    f"单个提示词不能超过 {ASR_HOTWORD_MAX_ENTRY_CHARACTERS} 个字符"
                    f"（第 {index} 项）"
                )
            if item in seen:
                continue

            seen.add(item)
            normalized.append(item)
            total_characters += len(item)
            if len(normalized) > ASR_HOTWORD_MAX_ENTRIES:
                raise ValueError(
                    f"提示词不能超过 {ASR_HOTWORD_MAX_ENTRIES} 条"
                )
            if total_characters > ASR_HOTWORD_MAX_TOTAL_CHARACTERS:
                raise ValueError(
                    "提示词总字符数不能超过 "
                    f"{ASR_HOTWORD_MAX_TOTAL_CHARACTERS} 个"
                )
        return normalized


class LegacyASRSettings(BaseModel):
    """Read-only shape for persisted Qwen jobs created by older versions."""

    provider: Literal["qwen3_asr"] = "qwen3_asr"
    model: Literal["qwen3-asr-1.7b"] = "qwen3-asr-1.7b"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    vad_filter: bool = True
    beam_size: int = Field(default=5, ge=1, le=20)
    output_mode: Literal[ASROutputMode.WORD_RESEGMENTED] = ASROutputMode.WORD_RESEGMENTED

    @field_validator("compute_type")
    @classmethod
    def non_empty_compute_type(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class TranslationSettings(BaseModel):
    provider: TranslationProviderName = TranslationProviderName.CODEX_SPARK
    model: str | None = None
    endpoint: str | None = None
    api_key: SecretStr | None = Field(default=None, repr=False)
    timeout_seconds: float = Field(default=300, ge=10, le=3600)

    @field_validator("model", "endpoint", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def provider_requirements(self) -> TranslationSettings:
        if self.provider == TranslationProviderName.LM_STUDIO and not self.model:
            raise ValueError("LM Studio 需要填写本地模型 ID")
        return self


class MediaStepSettings(BaseModel):
    source_kind: SourceKind
    path: str
    name: str

    @field_validator("path", "name")
    @classmethod
    def non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class TranslationStepSettings(BaseModel):
    target_language: TargetLanguage = TargetLanguage.ZH_CN
    provider: TranslationProviderName = TranslationProviderName.CODEX_SPARK
    model: str | None = None
    endpoint: str | None = None
    timeout_seconds: float = Field(default=300, ge=10, le=3600)

    @field_validator("model", "endpoint", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def provider_requirements(self) -> TranslationStepSettings:
        if self.provider == TranslationProviderName.LM_STUDIO and not self.model:
            raise ValueError("LM Studio 需要填写本地模型 ID")
        return self

    def runtime_settings(self, api_key: SecretStr | None = None) -> TranslationSettings:
        return TranslationSettings(
            provider=self.provider,
            model=self.model,
            endpoint=self.endpoint,
            timeout_seconds=self.timeout_seconds,
            api_key=api_key,
        )


class ExportSettings(BaseModel):
    output_directory: str | None = None
    overwrite_existing: bool = True
    format: Literal["srt"] = "srt"
    bilingual_order: Literal["source_then_translation"] = "source_then_translation"

    @field_validator("output_directory", mode="before")
    @classmethod
    def strip_optional_directory(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class JobCreateRequest(BaseModel):
    video_path: str | None = None
    upload_id: str | None = None
    target_language: TargetLanguage = TargetLanguage.ZH_CN
    asr: ASRSettings = Field(default_factory=ASRSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    auto_start: bool = False

    @model_validator(mode="after")
    def exactly_one_source(self) -> JobCreateRequest:
        if bool(self.video_path) == bool(self.upload_id):
            raise ValueError("video_path 与 upload_id 必须且只能填写一个")
        return self


class SubtitleSegment(BaseModel):
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def valid_range(self) -> SubtitleSegment:
        if self.end_ms <= self.start_ms:
            raise ValueError("字幕结束时间必须晚于开始时间")
        if not self.id.strip():
            raise ValueError("字幕 ID 不能为空")
        if not self.text.strip():
            raise ValueError("字幕文本不能为空")
        return self


class TranslationItem(BaseModel):
    id: str
    text: str


class TranslatedItem(BaseModel):
    id: str
    translated_text: str


class ModelUsageSummary(BaseModel):
    provider: str
    model: str | None = None
    request_count: int = Field(ge=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    source: Literal["provider", "cli", "unavailable", "mixed"]
    complete: bool

    @field_validator("provider")
    @classmethod
    def non_empty_provider(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Provider 不能为空")
        return value

    @field_validator("model", mode="before")
    @classmethod
    def strip_optional_model(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


def merge_model_usage(
    summaries: list[ModelUsageSummary],
) -> ModelUsageSummary | None:
    """Merge reported values without turning unknown usage into zero."""
    if not summaries:
        return None

    def common_or_none(values: set[str | None]) -> str | None:
        return next(iter(values)) if len(values) == 1 else None

    def sum_reported(field_name: str) -> int | None:
        values = [getattr(item, field_name) for item in summaries]
        reported = [value for value in values if value is not None]
        return sum(reported) if reported else None

    providers = {item.provider for item in summaries}
    sources = {item.source for item in summaries}
    provider = next(iter(providers)) if len(providers) == 1 else "multiple"
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    return ModelUsageSummary(
        provider=provider,
        model=common_or_none({item.model for item in summaries}),
        request_count=sum(item.request_count for item in summaries),
        input_tokens=sum_reported("input_tokens"),
        output_tokens=sum_reported("output_tokens"),
        total_tokens=sum_reported("total_tokens"),
        cached_input_tokens=sum_reported("cached_input_tokens"),
        reasoning_tokens=sum_reported("reasoning_tokens"),
        source=source,
        complete=all(item.complete for item in summaries),
    )


class LogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    level: LogLevel = LogLevel.INFO
    message: str


class StepArtifactView(BaseModel):
    id: str
    step: JobStep
    path: str
    fingerprint: str
    config_fingerprint: str
    input_fingerprints: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    summary: dict[str, Any] = Field(default_factory=dict)


class StepAttemptView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    number: int = Field(ge=1)
    status: StepStatus
    config: dict[str, Any]
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    model_usage: ModelUsageSummary | None = None
    artifact_id: str | None = None
    error: str | None = None


class JobStepView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: JobStep
    status: StepStatus
    progress: int = Field(default=0, ge=0, le=100)
    config_revision: int = Field(default=1, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)
    attempts: list[StepAttemptView] = Field(default_factory=list)
    artifact: StepArtifactView | None = None
    error: str | None = None
    can_run: bool = False
    latest_duration_ms: int | None = Field(default=None, ge=0)
    total_duration_ms: int | None = Field(default=None, ge=0)
    total_model_usage: ModelUsageSummary | None = None


class JobView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    status: JobStatus
    stage: JobStage
    progress: int = Field(ge=0, le=100)
    source_name: str
    source_kind: SourceKind
    detected_language: str | None = None
    target_language: TargetLanguage
    asr_provider: ASRProviderName | Literal["qwen3_asr"]
    translation_provider: TranslationProviderName
    created_at: datetime
    updated_at: datetime
    subtitle_path: str | None = None
    error: str | None = None
    logs: list[LogEntry] = Field(default_factory=list)
    steps: list[JobStepView] = Field(default_factory=list)
    wall_duration_ms: int | None = Field(default=None, ge=0)
    cumulative_attempt_duration_ms: int | None = Field(default=None, ge=0)
    total_model_usage: ModelUsageSummary | None = None


class JobRunRequest(BaseModel):
    api_key: SecretStr | None = Field(default=None, repr=False)
    continue_pipeline: bool = True


class JobStepConfigUpdate(BaseModel):
    config: dict[str, Any]


class JobDeleteResult(BaseModel):
    deleted: bool
    job_id: str


class UploadView(BaseModel):
    upload_id: str
    name: str
    path: str
    size: int


class PickVideoResult(BaseModel):
    selected: bool
    path: str | None = None
    name: str | None = None
    size: int | None = None


class OpenFolderRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def non_empty_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path 不能为空")
        return value


class OpenFolderResult(BaseModel):
    opened: bool
    path: str


class ResolvedSource(BaseModel):
    kind: SourceKind
    path: Path
    name: str

    model_config = {"arbitrary_types_allowed": True}
