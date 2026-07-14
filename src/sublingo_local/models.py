from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class TranslationProviderName(StrEnum):
    CODEX_SPARK = "codex_spark"
    LM_STUDIO = "lmstudio"
    DEEPSEEK = "deepseek"


class TargetLanguage(StrEnum):
    ZH_CN = "zh-CN"
    EN = "en"
    KO = "ko"


class SourceKind(StrEnum):
    PATH = "path"
    UPLOAD = "upload"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LogLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ASRSettings(BaseModel):
    model: str = "small"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    vad_filter: bool = True
    beam_size: int = Field(default=5, ge=1, le=20)

    @field_validator("model", "compute_type")
    @classmethod
    def non_empty(cls, value: str) -> str:
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
        if self.provider == TranslationProviderName.DEEPSEEK and (
            not self.api_key or not self.api_key.get_secret_value().strip()
        ):
            raise ValueError("DeepSeek 需要 API Key")
        return self


class JobCreateRequest(BaseModel):
    video_path: str | None = None
    upload_id: str | None = None
    target_language: TargetLanguage = TargetLanguage.ZH_CN
    asr: ASRSettings = Field(default_factory=ASRSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)

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


class LogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    level: LogLevel = LogLevel.INFO
    message: str


class JobView(BaseModel):
    id: str
    status: JobStatus
    stage: JobStage
    progress: int = Field(ge=0, le=100)
    source_name: str
    source_kind: SourceKind
    detected_language: str | None = None
    target_language: TargetLanguage
    translation_provider: TranslationProviderName
    created_at: datetime
    updated_at: datetime
    subtitle_path: str | None = None
    error: str | None = None
    logs: list[LogEntry] = Field(default_factory=list)


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
