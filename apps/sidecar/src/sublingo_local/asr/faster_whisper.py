from __future__ import annotations

import gc
import math
import os
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from ..model_manager import ModelManager
from ..models import ASROutputMode, ASRSettings, SubtitleSegment
from .base import ASRProgress, ASRProvider, TranscriptionResult
from .diagnostics import (
    ASRAudioAnalysis,
    ASRDiagnosticsSummary,
    ASRRunDiagnostics,
    ASRSegmentDiagnostics,
    ASRWindowDiagnostics,
    AudioInterval,
    normalize_intervals,
)

_SAMPLE_RATE = 16_000
_CORE_SECONDS = 60.0
_CONTEXT_SECONDS = 2.0
_MIN_CORE_SECONDS = 45.0
_MAX_CORE_SECONDS = 75.0
_MIN_BOUNDARY_SILENCE_SECONDS = 0.35
_LANGUAGE_PROBE_SECONDS = 30.0
_MAX_CUE_SECONDS = 7.0
_MAX_CUE_GAP_SECONDS = 1.2
_MIN_CUE_DURATION_MS = 400
_CORE_SAMPLES = round(_CORE_SECONDS * _SAMPLE_RATE)
_CONTEXT_SAMPLES = round(_CONTEXT_SECONDS * _SAMPLE_RATE)
_MIN_CORE_SAMPLES = round(_MIN_CORE_SECONDS * _SAMPLE_RATE)
_MAX_CORE_SAMPLES = round(_MAX_CORE_SECONDS * _SAMPLE_RATE)
_MIN_BOUNDARY_SILENCE_SAMPLES = round(_MIN_BOUNDARY_SILENCE_SECONDS * _SAMPLE_RATE)
_MAX_SILENCE_BONUS_SAMPLES = round(2.0 * _SAMPLE_RATE)
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
    boundary_shift_samples: int = 0
    fallback_to_fixed: bool = False


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


def _optional_finite_float(value: object) -> float | None:
    try:
        metric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return metric if math.isfinite(metric) else None


def _optional_probability(value: object) -> float | None:
    metric = _optional_finite_float(value)
    return metric if metric is not None and 0.0 <= metric <= 1.0 else None


def _optional_non_negative_float(value: object) -> float | None:
    metric = _optional_finite_float(value)
    return metric if metric is not None and metric >= 0.0 else None


def _valid_word_offsets(start: float, end: float, *, window_duration: float) -> bool:
    return 0.0 <= start < end <= window_duration


def _sample_interval(start: float, end: float, *, total_samples: int) -> AudioInterval:
    start_sample = max(0, min(total_samples - 1, round(start * _SAMPLE_RATE)))
    end_sample = max(start_sample + 1, min(total_samples, round(end * _SAMPLE_RATE)))
    return AudioInterval(start_sample=start_sample, end_sample=end_sample)


def _make_chunk_window(
    index: int,
    *,
    core_start_sample: int,
    core_end_sample: int,
    total_samples: int,
    boundary_shift_samples: int = 0,
    fallback_to_fixed: bool = False,
) -> _ChunkWindow:
    context_start_sample = max(0, core_start_sample - _CONTEXT_SAMPLES)
    context_end_sample = min(total_samples, core_end_sample + _CONTEXT_SAMPLES)
    return _ChunkWindow(
        index=index,
        core_start=core_start_sample / _SAMPLE_RATE,
        core_end=core_end_sample / _SAMPLE_RATE,
        context_start=context_start_sample / _SAMPLE_RATE,
        context_end=context_end_sample / _SAMPLE_RATE,
        sample_start=context_start_sample,
        sample_end=context_end_sample,
        boundary_shift_samples=boundary_shift_samples,
        fallback_to_fixed=fallback_to_fixed,
    )


def _chunk_windows(total_samples: int) -> list[_ChunkWindow]:
    """Return the legacy deterministic 60-second fixed windows."""

    if total_samples <= 0:
        return []
    windows: list[_ChunkWindow] = []
    core_start_sample = 0
    while core_start_sample < total_samples:
        core_end_sample = min(total_samples, core_start_sample + _CORE_SAMPLES)
        windows.append(
            _make_chunk_window(
                len(windows),
                core_start_sample=core_start_sample,
                core_end_sample=core_end_sample,
                total_samples=total_samples,
            )
        )
        core_start_sample = core_end_sample
    return windows


def _fixed_fallback_windows(total_samples: int) -> list[_ChunkWindow]:
    fixed = _chunk_windows(total_samples)
    return [
        _make_chunk_window(
            window.index,
            core_start_sample=round(window.core_start * _SAMPLE_RATE),
            core_end_sample=round(window.core_end * _SAMPLE_RATE),
            total_samples=total_samples,
            fallback_to_fixed=(window.index < len(fixed) - 1 or len(fixed) == 1),
        )
        for window in fixed
    ]


def _feasible_cut_ranges(
    core_start_sample: int,
    *,
    total_samples: int,
) -> tuple[tuple[int, int], ...]:
    """Return cut ranges whose current and remaining cores can stay within bounds."""

    lower = core_start_sample + _MIN_CORE_SAMPLES
    upper = min(core_start_sample + _MAX_CORE_SAMPLES, total_samples - _MIN_CORE_SAMPLES)
    if upper < lower:
        return ()

    ranges: list[tuple[int, int]] = []
    long_tail_end = min(upper, total_samples - 2 * _MIN_CORE_SAMPLES)
    if long_tail_end >= lower:
        ranges.append((lower, long_tail_end))

    final_core_start = max(lower, total_samples - _MAX_CORE_SAMPLES)
    final_core_end = min(upper, total_samples - _MIN_CORE_SAMPLES)
    if final_core_end >= final_core_start:
        ranges.append((final_core_start, final_core_end))

    merged: list[list[int]] = []
    for start_sample, end_sample in sorted(ranges):
        if merged and start_sample <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end_sample)
        else:
            merged.append([start_sample, end_sample])
    return tuple((start_sample, end_sample) for start_sample, end_sample in merged)


def _select_silence_cut(
    non_speech_intervals: Sequence[AudioInterval],
    feasible_ranges: Sequence[tuple[int, int]],
    *,
    target_sample: int,
) -> int | None:
    candidates: list[tuple[int, int, int, int]] = []
    for silence in non_speech_intervals:
        silence_length = silence.end_sample - silence.start_sample
        if silence_length < _MIN_BOUNDARY_SILENCE_SAMPLES:
            continue
        for range_start, range_end in feasible_ranges:
            intersection_start = max(silence.start_sample, range_start)
            intersection_end = min(silence.end_sample, range_end)
            if intersection_end < intersection_start:
                continue
            cut_sample = (intersection_start + intersection_end) // 2
            distance = abs(cut_sample - target_sample)
            length_bonus = min(silence_length, _MAX_SILENCE_BONUS_SAMPLES) // 4
            candidates.append(
                (distance - length_bonus, distance, -silence_length, cut_sample)
            )
    return min(candidates)[-1] if candidates else None


def _dynamic_chunk_windows(
    total_samples: int,
    non_speech_intervals: Sequence[AudioInterval],
) -> list[_ChunkWindow]:
    """Snap 60-second boundaries to nearby silence or fall back to all fixed windows."""

    if total_samples <= 0:
        return []
    if total_samples < _MIN_CORE_SAMPLES:
        return _fixed_fallback_windows(total_samples)
    if total_samples <= _MAX_CORE_SAMPLES:
        return [
            _make_chunk_window(
                0,
                core_start_sample=0,
                core_end_sample=total_samples,
                total_samples=total_samples,
            )
        ]

    windows: list[_ChunkWindow] = []
    core_start_sample = 0
    while total_samples - core_start_sample > _MAX_CORE_SAMPLES:
        feasible_ranges = _feasible_cut_ranges(
            core_start_sample,
            total_samples=total_samples,
        )
        target_sample = core_start_sample + _CORE_SAMPLES
        cut_sample = _select_silence_cut(
            non_speech_intervals,
            feasible_ranges,
            target_sample=target_sample,
        )
        if cut_sample is None or cut_sample <= core_start_sample:
            return _fixed_fallback_windows(total_samples)
        windows.append(
            _make_chunk_window(
                len(windows),
                core_start_sample=core_start_sample,
                core_end_sample=cut_sample,
                total_samples=total_samples,
                boundary_shift_samples=cut_sample - target_sample,
            )
        )
        core_start_sample = cut_sample

    windows.append(
        _make_chunk_window(
            len(windows),
            core_start_sample=core_start_sample,
            core_end_sample=total_samples,
            total_samples=total_samples,
        )
    )
    return windows


def _analyze_vad(
    audio: Any,
    *,
    get_speech_timestamps: Any,
    vad_options_type: Any,
) -> ASRAudioAnalysis:
    raw_intervals = get_speech_timestamps(
        audio,
        vad_options=vad_options_type(
            threshold=0.5,
            min_speech_duration_ms=0,
            max_speech_duration_s=math.inf,
            min_silence_duration_ms=round(_MIN_BOUNDARY_SILENCE_SECONDS * 1_000),
            speech_pad_ms=0,
        ),
        sampling_rate=_SAMPLE_RATE,
    )
    speech_intervals: list[tuple[int, int]] = []
    for value in raw_intervals:
        if not isinstance(value, Mapping) or "start" not in value or "end" not in value:
            raise ValueError("VAD 返回了无效区间")
        speech_intervals.append((int(value["start"]), int(value["end"])))
    return ASRAudioAnalysis(
        sample_rate=_SAMPLE_RATE,
        total_samples=len(audio),
        vad_source="faster_whisper",
        vad_status="available",
        speech_intervals=normalize_intervals(
            speech_intervals,
            total_samples=len(audio),
        ),
    )


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
            fixed_windows = _chunk_windows(len(audio))
            if not fixed_windows:
                raise RuntimeError("音频为空，无法执行 Faster-Whisper")
            if settings.dynamic_chunking:
                window_strategy: Literal["fixed", "vad_dynamic"] = "vad_dynamic"
                try:
                    from faster_whisper.vad import VadOptions, get_speech_timestamps

                    audio_analysis = _analyze_vad(
                        audio,
                        get_speech_timestamps=get_speech_timestamps,
                        vad_options_type=VadOptions,
                    )
                    windows = _dynamic_chunk_windows(
                        len(audio),
                        audio_analysis.non_speech_intervals,
                    )
                except Exception:
                    audio_analysis = ASRAudioAnalysis.failed(
                        sample_rate=_SAMPLE_RATE,
                        total_samples=len(audio),
                        vad_source="faster_whisper",
                    )
                    windows = _fixed_fallback_windows(len(audio))
            else:
                window_strategy = "fixed"
                audio_analysis = ASRAudioAnalysis.unavailable(
                    sample_rate=_SAMPLE_RATE,
                    total_samples=len(audio),
                    vad_source="disabled",
                )
                windows = fixed_windows
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
            candidate_diagnostics: list[ASRSegmentDiagnostics] = []
            window_candidate_counts = [0 for _ in windows]
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

                for raw_segment_index, raw_segment in enumerate(chunk_segments):
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
                    raw_words = [
                        raw_word
                        for raw_word in (getattr(raw_segment, "words", None) or [])
                        if str(getattr(raw_word, "word", "")).strip()
                    ]
                    for raw_word in raw_words:
                        word_text = str(getattr(raw_word, "word", ""))
                        relative_word_start = _optional_finite_float(
                            getattr(raw_word, "start", None)
                        )
                        relative_word_end = _optional_finite_float(
                            getattr(raw_word, "end", None)
                        )
                        if relative_word_start is None or relative_word_end is None:
                            continue
                        if not _valid_word_offsets(
                            relative_word_start,
                            relative_word_end,
                            window_duration=window.context_end - window.context_start,
                        ):
                            continue
                        word_start = max(
                            0.0, window.context_start + relative_word_start
                        )
                        word_end = min(
                            duration,
                            window.context_start + relative_word_end,
                        )
                        words.append(
                            _WordItem(
                                text=word_text,
                                start=word_start,
                                end=max(word_start + 0.001, word_end),
                            )
                        )
                    avg_logprob = _optional_finite_float(
                        getattr(raw_segment, "avg_logprob", None)
                    )
                    candidates.append(
                        _ChunkSegment(
                            text=text,
                            start=start,
                            end=end,
                            words=tuple(words),
                            chunk_index=window.index,
                            avg_logprob=(
                                avg_logprob if avg_logprob is not None else float("-inf")
                            ),
                        )
                    )
                    candidate_diagnostics.append(
                        ASRSegmentDiagnostics(
                            candidate_id=(
                                "candidate-"
                                f"chunk-{window.index:06d}-segment-{raw_segment_index:06d}"
                            ),
                            window_index=window.index,
                            interval=_sample_interval(
                                start,
                                end,
                                total_samples=len(audio),
                            ),
                            avg_logprob=avg_logprob,
                            no_speech_prob=_optional_probability(
                                getattr(raw_segment, "no_speech_prob", None)
                            ),
                            compression_ratio=_optional_non_negative_float(
                                getattr(raw_segment, "compression_ratio", None)
                            ),
                            temperature=_optional_non_negative_float(
                                getattr(raw_segment, "temperature", None)
                            ),
                            word_count=len(raw_words),
                            valid_word_timestamp_count=len(words),
                            word_timestamp_coverage=(
                                len(words) / len(raw_words) if raw_words else 0.0
                            ),
                        )
                    )
                    window_candidate_counts[window.index] += 1
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
            window_diagnostics = tuple(
                ASRWindowDiagnostics(
                    index=window.index,
                    core=AudioInterval(
                        start_sample=round(window.core_start * _SAMPLE_RATE),
                        end_sample=round(window.core_end * _SAMPLE_RATE),
                    ),
                    context=AudioInterval(
                        start_sample=window.sample_start,
                        end_sample=window.sample_end,
                    ),
                    boundary_shift_samples=window.boundary_shift_samples,
                    fallback_to_fixed=window.fallback_to_fixed,
                    candidate_count=window_candidate_counts[window.index],
                )
                for window in windows
            )
            diagnostics = ASRRunDiagnostics(
                window_strategy=window_strategy,
                audio=audio_analysis,
                windows=window_diagnostics,
                segments=tuple(candidate_diagnostics),
                summary=ASRDiagnosticsSummary(
                    window_count=len(window_diagnostics),
                    fallback_window_count=sum(
                        window.fallback_to_fixed for window in windows
                    ),
                    boundary_shift_abs_total_samples=sum(
                        abs(window.boundary_shift_samples) for window in windows
                    ),
                    candidate_segment_count=len(candidate_diagnostics),
                    deduplicated_segment_count=len(candidates) - len(chunk_segments),
                    output_segment_count=len(segments),
                ),
            )
            return TranscriptionResult(
                language=detected_language,
                language_probability=detected_probability,
                duration_seconds=duration,
                segments=segments,
                diagnostics=diagnostics,
            )
        finally:
            audio = None
            model = None
            gc.collect()
