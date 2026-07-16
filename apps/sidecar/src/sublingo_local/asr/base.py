from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, model_validator

from ..models import ASRSettings, SubtitleSegment
from .diagnostics import ASRRunDiagnostics

ASRProgress = Callable[[float], None]


class TranscriptionResult(BaseModel):
    language: str
    language_probability: float | None = None
    duration_seconds: float | None = None
    segments: list[SubtitleSegment]
    diagnostics: ASRRunDiagnostics | None = None

    @model_validator(mode="after")
    def diagnostics_match_output(self) -> TranscriptionResult:
        if (
            self.diagnostics is not None
            and self.diagnostics.summary.output_segment_count != len(self.segments)
        ):
            raise ValueError("ASR 输出片段汇总数量与字幕明细不一致")
        return self


class ASRProvider(ABC):
    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str,
        settings: ASRSettings,
        on_progress: ASRProgress | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file. Implementations may block."""
