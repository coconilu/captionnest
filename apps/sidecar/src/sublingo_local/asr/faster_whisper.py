from __future__ import annotations

import gc
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..model_manager import ModelManager
from ..models import ASROutputMode, ASRSettings, SubtitleSegment
from .base import ASRProgress, ASRProvider, TranscriptionResult

_SAMPLE_RATE = 16_000
_CORE_SECONDS = 60.0
_CONTEXT_SECONDS = 2.0
_LANGUAGE_PROBE_SECONDS = 30.0
_MAX_CUE_SECONDS = 7.0
_MAX_CUE_GAP_SECONDS = 1.2
_MIN_CUE_DURATION_MS = 400
_TERMINAL_PUNCTUATION = ("。", "！", "？", "!", "?", ".", "…")
_CJK_LANGUAGES = {"zh", "yue", "ja", "ko", "chinese", "japanese", "korean"}
_TEXT_KEY_PATTERN = re.compile(r"[\s、。,.!?！？「」『』（）()…〜~・]+")


@dataclass(frozen=True)
class _ChunkWindow:
    index: int
    core_start: float
    core_end: float
    context_start: float
    context_end: float
    sample_start: int
    sample_end: int


@dataclass(frozen=True)
class _WordItem:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class _ChunkSegment:
    text: str
    start: float
    end: float
    words: tuple[_WordItem, ...]
    chunk_index: int
    avg_logprob: float


def _chunk_windows(total_samples: int) -> list[_ChunkWindow]:
    if total_samples <= 0:
        return []
    duration = total_samples / _SAMPLE_RATE
    count = math.ceil(duration / _CORE_SECONDS)
    windows: list[_ChunkWindow] = []
    for index in range(count):
        core_start = index * _CORE_SECONDS
        core_end = min(duration, (index + 1) * _CORE_SECONDS)
        context_start = max(0.0, core_start - _CONTEXT_SECONDS)
        context_end = min(duration, core_end + _CONTEXT_SECONDS)
        windows.append(
            _ChunkWindow(
                index=index,
                core_start=core_start,
                core_end=core_end,
                context_start=context_start,
                context_end=context_end,
                sample_start=round(context_start * _SAMPLE_RATE),
                sample_end=round(context_end * _SAMPLE_RATE),
            )
        )
    return windows


def _text_key(value: str) -> str:
    return _TEXT_KEY_PATTERN.sub("", value)


def _same_boundary_utterance(left: _ChunkSegment, right: _ChunkSegment) -> bool:
    if left.chunk_index == right.chunk_index:
        return False
    time_gap = max(left.start, right.start) - min(left.end, right.end)
    if time_gap > 0.8:
        return False
    left_text = _text_key(left.text)
    right_text = _text_key(right.text)
    if not left_text or not right_text:
        return False
    overlap = min(left.end, right.end) - max(left.start, right.start)
    if left_text == right_text and (min(len(left_text), len(right_text)) >= 2 or overlap > 0.2):
        return True
    if min(len(left_text), len(right_text)) >= 3 and (
        left_text in right_text or right_text in left_text
    ):
        return True
    return (
        min(len(left_text), len(right_text)) >= 4
        and SequenceMatcher(None, left_text, right_text).ratio() >= 0.84
    )


def _deduplicate_boundary_segments(items: list[_ChunkSegment]) -> list[_ChunkSegment]:
    kept: list[_ChunkSegment] = []
    for item in sorted(items, key=lambda value: (value.start, value.end, value.chunk_index)):
        duplicate_index: int | None = None
        for index in range(max(0, len(kept) - 8), len(kept)):
            if _same_boundary_utterance(kept[index], item):
                duplicate_index = index
                break
        if duplicate_index is None:
            kept.append(item)
        elif item.avg_logprob > kept[duplicate_index].avg_logprob:
            kept[duplicate_index] = item
    return sorted(kept, key=lambda value: (value.start, value.end))


def _subtitle_segment(index: int, *, start: float, end: float, text: str) -> SubtitleSegment:
    start_ms = max(0, round(start * 1_000))
    end_ms = max(start_ms + 1, round(end * 1_000))
    return SubtitleSegment(
        id=f"seg-{index:06d}",
        start_ms=start_ms,
        end_ms=end_ms,
        text=text.strip(),
    )


def _chunk_segments_to_subtitles(items: list[_ChunkSegment]) -> list[SubtitleSegment]:
    return [
        _subtitle_segment(index, start=item.start, end=item.end, text=item.text)
        for index, item in enumerate(items, start=1)
    ]


def _flush_word_group(
    cues: list[tuple[float, float, str]], current: list[_WordItem]
) -> None:
    if not current:
        return
    text = "".join(word.text for word in current).strip()
    if text:
        cues.append((current[0].start, current[-1].end, text))
    current.clear()


def _word_resegmented_subtitles(
    items: list[_ChunkSegment],
    *,
    language: str,
    duration_seconds: float,
) -> list[SubtitleSegment]:
    normalized_language = language.strip().lower().replace("_", "-").split("-", 1)[0]
    max_characters = 28 if normalized_language in _CJK_LANGUAGES else 52
    cues: list[tuple[float, float, str]] = []

    for parent in items:
        joined_words = "".join(word.text for word in parent.words).strip()
        if not parent.words or re.sub(r"\s+", "", joined_words) != re.sub(
            r"\s+", "", parent.text
        ):
            cues.append((parent.start, parent.end, parent.text))
            continue

        current: list[_WordItem] = []

        for word in parent.words:
            if current:
                gap = max(0.0, word.start - current[-1].end)
                proposed_duration = word.end - current[0].start
                proposed_text = "".join(item.text for item in current) + word.text
                if (
                    gap >= _MAX_CUE_GAP_SECONDS
                    or proposed_duration > _MAX_CUE_SECONDS
                    or len(re.sub(r"\s+", "", proposed_text)) > max_characters
                ):
                    _flush_word_group(cues, current)
            current.append(word)
            current_text = "".join(item.text for item in current).rstrip()
            if (
                current_text.endswith(_TERMINAL_PUNCTUATION)
                and word.end - current[0].start >= 0.45
            ):
                _flush_word_group(cues, current)
        _flush_word_group(cues, current)

    raw_segments = [
        _subtitle_segment(index, start=start, end=end, text=text)
        for index, (start, end, text) in enumerate(sorted(cues), start=1)
    ]
    readable: list[SubtitleSegment] = []
    duration_ms = max(1, round(duration_seconds * 1_000))
    for index, segment in enumerate(raw_segments):
        desired_end = min(duration_ms, segment.start_ms + _MIN_CUE_DURATION_MS)
        if index + 1 < len(raw_segments) and raw_segments[index + 1].start_ms > segment.end_ms:
            desired_end = min(desired_end, raw_segments[index + 1].start_ms - 1)
        readable.append(
            segment.model_copy(
                update={
                    "id": f"seg-{index + 1:06d}",
                    "end_ms": max(segment.end_ms, desired_end),
                }
            )
        )
    return readable


def _probe_language(model: Any, audio: Any) -> tuple[str | None, float | None]:
    detect_language = getattr(model, "detect_language", None)
    if not callable(detect_language):
        return None, None
    total_samples = len(audio)
    probe_samples = round(_LANGUAGE_PROBE_SECONDS * _SAMPLE_RATE)
    max_start = max(0, total_samples - probe_samples)
    starts = sorted({round(max_start * fraction) for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)})
    scores: defaultdict[str, float] = defaultdict(float)
    probabilities: defaultdict[str, list[float]] = defaultdict(list)
    for start in starts:
        clip = audio[start : min(total_samples, start + probe_samples)]
        try:
            language, probability, _ = detect_language(
                audio=clip,
                vad_filter=True,
                language_detection_segments=1,
            )
        except (RuntimeError, ValueError):
            continue
        normalized = str(language or "").strip()
        if not normalized:
            continue
        confidence = max(0.01, float(probability or 0.0))
        scores[normalized] += confidence
        probabilities[normalized].append(confidence)
    if not scores:
        return None, None
    detected = max(scores, key=scores.__getitem__)
    detected_probability = sum(probabilities[detected]) / len(probabilities[detected])
    return detected, detected_probability


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
            from faster_whisper.audio import decode_audio
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
        model: Any | None = None
        audio: Any | None = None
        try:
            model = WhisperModel(model_reference, device=device, compute_type=compute_type)
            audio = decode_audio(str(audio_path), sampling_rate=_SAMPLE_RATE)
            duration = len(audio) / _SAMPLE_RATE
            windows = _chunk_windows(len(audio))
            if not windows:
                raise RuntimeError("音频为空，无法执行 Faster-Whisper")
            if on_progress:
                on_progress(0.03)

            requested_language = None if language == "auto" else language
            detected_language = requested_language
            detected_probability: float | None = None
            if detected_language is None:
                detected_language, detected_probability = _probe_language(model, audio)
            if on_progress:
                on_progress(0.08)

            candidates: list[_ChunkSegment] = []
            fallback_languages: list[str] = []
            fallback_probabilities: list[float] = []
            for window_index, window in enumerate(windows, start=1):
                iterator, info = model.transcribe(
                    audio[window.sample_start : window.sample_end],
                    language=detected_language,
                    task="transcribe",
                    beam_size=settings.beam_size,
                    vad_filter=settings.vad_filter,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                chunk_segments = list(iterator)
                info_language = str(getattr(info, "language", "") or "").strip()
                info_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
                if chunk_segments and info_language:
                    fallback_languages.append(info_language)
                    fallback_probabilities.append(info_probability)
                    if detected_language is None:
                        detected_language = info_language
                        detected_probability = info_probability

                for raw_segment in chunk_segments:
                    text = str(getattr(raw_segment, "text", "")).strip()
                    if not text:
                        continue
                    start = max(0.0, window.context_start + float(raw_segment.start))
                    end = min(duration, window.context_start + float(raw_segment.end))
                    end = max(start + 0.001, end)
                    midpoint = (start + end) / 2
                    if midpoint < window.core_start or (
                        midpoint >= window.core_end and window.index < len(windows) - 1
                    ):
                        continue
                    words: list[_WordItem] = []
                    for raw_word in getattr(raw_segment, "words", None) or []:
                        word_text = str(getattr(raw_word, "word", ""))
                        if not word_text.strip():
                            continue
                        word_start = max(
                            0.0, window.context_start + float(getattr(raw_word, "start", 0.0))
                        )
                        word_end = min(
                            duration,
                            window.context_start + float(getattr(raw_word, "end", 0.0)),
                        )
                        words.append(
                            _WordItem(
                                text=word_text,
                                start=word_start,
                                end=max(word_start + 0.001, word_end),
                            )
                        )
                    candidates.append(
                        _ChunkSegment(
                            text=text,
                            start=start,
                            end=end,
                            words=tuple(words),
                            chunk_index=window.index,
                            avg_logprob=float(getattr(raw_segment, "avg_logprob", float("-inf"))),
                        )
                    )
                if on_progress:
                    on_progress(0.08 + window_index / len(windows) * 0.92)

            chunk_segments = _deduplicate_boundary_segments(candidates)
            if not chunk_segments:
                raise RuntimeError("没有从音频中识别出有效语音")
            if detected_language is None and fallback_languages:
                detected_language = Counter(fallback_languages).most_common(1)[0][0]
                detected_probability = max(fallback_probabilities, default=0.0)
            detected_language = detected_language or language
            segments = (
                _word_resegmented_subtitles(
                    chunk_segments,
                    language=detected_language,
                    duration_seconds=duration,
                )
                if settings.output_mode == ASROutputMode.WORD_RESEGMENTED
                else _chunk_segments_to_subtitles(chunk_segments)
            )
            return TranscriptionResult(
                language=detected_language,
                language_probability=detected_probability,
                duration_seconds=duration,
                segments=segments,
            )
        finally:
            audio = None
            model = None
            gc.collect()
