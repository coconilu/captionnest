from __future__ import annotations

import gc
import os
from pathlib import Path

from ..model_manager import ModelManager
from ..models import ASRSettings, SubtitleSegment
from .base import ASRProgress, ASRProvider, TranscriptionResult


class FasterWhisperProvider(ASRProvider):
    """Faster-Whisper adapter with a deliberately lazy heavyweight import."""

    def __init__(self, model_manager: ModelManager | None = None) -> None:
        self.model_manager = model_manager

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str,
        settings: ASRSettings,
        on_progress: ASRProgress | None = None,
    ) -> TranscriptionResult:
        configured_endpoint = os.getenv("CAPTIONNEST_HF_ENDPOINT", "").strip()
        if configured_endpoint:
            os.environ["HF_ENDPOINT"] = configured_endpoint
        elif os.getenv("HF_ENDPOINT", "").rstrip("/") == "https://hf-mirror.com":
            # hf-mirror currently redirects model files to the official Hub. Hugging Face Hub
            # rejects that cross-host metadata redirect, so use the final endpoint directly.
            os.environ["HF_ENDPOINT"] = "https://huggingface.co"
        try:
            # Keep Web UI startup and unit tests independent from CUDA/CTranslate2.
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "尚未安装 Faster-Whisper，请执行 pip install 'captionnest[asr]'"
            ) from exc

        device = settings.device
        compute_type = "default" if settings.compute_type == "auto" else settings.compute_type
        model_reference = (
            str(self.model_manager.resolve_installed_path(settings.model))
            if self.model_manager
            else settings.model
        )
        model = WhisperModel(model_reference, device=device, compute_type=compute_type)
        try:
            iterator, info = model.transcribe(
                str(audio_path),
                language=None if language == "auto" else language,
                task="transcribe",
                beam_size=settings.beam_size,
                vad_filter=settings.vad_filter,
                word_timestamps=True,
                condition_on_previous_text=False,
            )
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            segments: list[SubtitleSegment] = []
            for index, segment in enumerate(iterator, start=1):
                text = str(segment.text).strip()
                if not text:
                    continue
                start_ms = max(0, round(float(segment.start) * 1_000))
                end_ms = max(start_ms + 1, round(float(segment.end) * 1_000))
                segments.append(
                    SubtitleSegment(
                        id=f"seg-{index:06d}",
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                    )
                )
                if on_progress and duration > 0:
                    on_progress(min(1.0, float(segment.end) / duration))
            if on_progress:
                on_progress(1.0)
            if not segments:
                raise RuntimeError("没有从音频中识别出有效语音")
            return TranscriptionResult(
                language=str(getattr(info, "language", language)),
                language_probability=getattr(info, "language_probability", None),
                duration_seconds=duration or None,
                segments=segments,
            )
        finally:
            del model
            gc.collect()
