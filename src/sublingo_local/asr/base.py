from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from ..models import ASRSettings, SubtitleSegment

ASRProgress = Callable[[float], None]


class TranscriptionResult(BaseModel):
    language: str
    language_probability: float | None = None
    duration_seconds: float | None = None
    segments: list[SubtitleSegment]


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

