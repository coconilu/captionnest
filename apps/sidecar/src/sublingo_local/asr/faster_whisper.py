from __future__ import annotations

import gc
import math
import os
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from ..model_manager import ModelManager
from ..models import ASROutputMode, ASRSettings, SubtitleSegment
from .base import ASRProgress, ASRProvider, TranscriptionResult
from .diagnostics import (
    ASRAudioAnalysis,
    ASRDiagnosticsSummary,
    ASRRetryRequestDiagnostics,
    ASRRunDiagnostics,
    ASRSegmentDiagnostics,
    ASRWindowDiagnostics,
    AudioInterval,
    normalize_intervals,
)
from .retry import (
    CandidateQualityFacts,
    RetryCandidateFacts,
    RetryRequestPlan,
    covered_samples,
    evaluate_retry_reasons,
    interval_coverage_ratio,
    intrinsic_reason_severity,
    meaningfully_improves_speech_coverage,
    plan_retry_requests,
    score_candidate_bundle,
    should_select_retry,
    text_repetition_score,
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


def _serialize_hotwords(hotwords: Sequence[str]) -> str | None:
    """Adapt the validated task list to Faster-Whisper's string parameter."""
    serialized = ", ".join(hotwords)
    return serialized or None


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
    candidate_id: str = ""


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


def _segment_bounds(
    raw_segment: Any,
    *,
    context_start: float,
    duration_seconds: float,
) -> tuple[float, float] | None:
    relative_start = _optional_finite_float(getattr(raw_segment, "start", None))
    relative_end = _optional_finite_float(getattr(raw_segment, "end", None))
    if relative_start is None or relative_end is None or relative_end <= relative_start:
        return None
    start = max(0.0, context_start + relative_start)
    end = min(duration_seconds, context_start + relative_end)
    if start >= duration_seconds or end <= start:
        return None
    return start, end


def _speech_intervals(
    audio_analysis: ASRAudioAnalysis,
) -> tuple[AudioInterval, ...] | None:
    return (
        audio_analysis.speech_intervals
        if audio_analysis.vad_status == "available"
        else None
    )


def _parse_candidate(
    raw_segment: Any,
    *,
    candidate_id: str,
    window_index: int,
    start: float,
    end: float,
    context_start: float,
    context_end: float,
    duration_seconds: float,
    total_samples: int,
    audio_analysis: ASRAudioAnalysis,
) -> tuple[_ChunkSegment, ASRSegmentDiagnostics] | None:
    text = str(getattr(raw_segment, "text", "")).strip()
    if not text:
        return None
    words: list[_WordItem] = []
    raw_words = [
        raw_word
        for raw_word in (getattr(raw_segment, "words", None) or [])
        if str(getattr(raw_word, "word", "")).strip()
    ]
    for raw_word in raw_words:
        word_text = str(getattr(raw_word, "word", ""))
        relative_word_start = _optional_finite_float(getattr(raw_word, "start", None))
        relative_word_end = _optional_finite_float(getattr(raw_word, "end", None))
        if relative_word_start is None or relative_word_end is None:
            continue
        if not _valid_word_offsets(
            relative_word_start,
            relative_word_end,
            window_duration=context_end - context_start,
        ):
            continue
        word_start = max(0.0, context_start + relative_word_start)
        word_end = min(duration_seconds, context_start + relative_word_end)
        if word_end <= word_start:
            continue
        words.append(_WordItem(text=word_text, start=word_start, end=word_end))

    interval = _sample_interval(start, end, total_samples=total_samples)
    avg_logprob = _optional_finite_float(getattr(raw_segment, "avg_logprob", None))
    speech = _speech_intervals(audio_analysis)
    diagnostics = ASRSegmentDiagnostics(
        candidate_id=candidate_id,
        window_index=window_index,
        interval=interval,
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
        word_timestamp_coverage=len(words) / len(raw_words) if raw_words else 0.0,
        text_repetition_score=text_repetition_score(text),
        vad_speech_coverage=(
            interval_coverage_ratio(interval, speech) if speech is not None else None
        ),
    )
    return (
        _ChunkSegment(
            text=text,
            start=start,
            end=end,
            words=tuple(words),
            chunk_index=window_index,
            avg_logprob=avg_logprob if avg_logprob is not None else float("-inf"),
            candidate_id=candidate_id,
        ),
        diagnostics,
    )


def _clip_retry_candidate_to_core(
    segment: _ChunkSegment,
    diagnostics: ASRSegmentDiagnostics,
    *,
    core: AudioInterval,
    total_samples: int,
    audio_analysis: ASRAudioAnalysis,
) -> tuple[_ChunkSegment, ASRSegmentDiagnostics] | None:
    """Keep only word-owned retry text inside the replaceable core."""

    core_start = core.start_sample / _SAMPLE_RATE
    core_end = core.end_sample / _SAMPLE_RATE
    segment_inside_core = (
        diagnostics.interval.start_sample >= core.start_sample
        and diagnostics.interval.end_sample <= core.end_sample
    )
    words_inside_core = all(
        word.start >= core_start and word.end <= core_end
        for word in segment.words
    )
    if segment_inside_core and words_inside_core:
        return segment, diagnostics

    owned_words: list[_WordItem] = []
    for word in segment.words:
        midpoint_sample = round((word.start + word.end) / 2 * _SAMPLE_RATE)
        if not core.start_sample <= midpoint_sample < core.end_sample:
            continue
        clipped_start = max(core_start, word.start)
        clipped_end = min(core_end, word.end)
        if clipped_end <= clipped_start:
            continue
        owned_words.append(
            replace(word, start=clipped_start, end=clipped_end)
        )
    if not owned_words:
        return None

    text = "".join(word.text for word in owned_words).strip()
    if not text:
        return None
    start = owned_words[0].start
    end = owned_words[-1].end
    interval = _sample_interval(start, end, total_samples=total_samples)
    speech = _speech_intervals(audio_analysis)
    clipped_diagnostics = diagnostics.model_copy(
        update={
            "interval": interval,
            "word_count": len(owned_words),
            "valid_word_timestamp_count": len(owned_words),
            "word_timestamp_coverage": 1.0,
            "text_repetition_score": text_repetition_score(text),
            "vad_speech_coverage": (
                interval_coverage_ratio(interval, speech)
                if speech is not None
                else None
            ),
        }
    )
    return (
        replace(
            segment,
            text=text,
            start=start,
            end=end,
            words=tuple(owned_words),
        ),
        clipped_diagnostics,
    )


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


def _preserve_equivalent_initial_timeline(
    initial: Sequence[_ChunkSegment],
    retry: Sequence[_ChunkSegment],
) -> list[_ChunkSegment]:
    """Keep program-owned timing when retry only improves confidence facts."""

    ordered_initial = sorted(initial, key=lambda item: (item.start, item.end))
    ordered_retry = sorted(retry, key=lambda item: (item.start, item.end))
    if len(ordered_initial) != len(ordered_retry) or any(
        not _text_key(original.text)
        or _text_key(original.text) != _text_key(replacement.text)
        for original, replacement in zip(
            ordered_initial,
            ordered_retry,
            strict=True,
        )
    ):
        return list(retry)
    return [
        replace(
            replacement,
            start=original.start,
            end=original.end,
            words=original.words,
            chunk_index=original.chunk_index,
        )
        for original, replacement in zip(
            ordered_initial,
            ordered_retry,
            strict=True,
        )
    ]


def _prepare_retry_plans(
    kept_segments: Sequence[_ChunkSegment],
    candidate_diagnostics: list[ASRSegmentDiagnostics],
    *,
    audio_analysis: ASRAudioAnalysis,
    total_samples: int,
    enabled: bool,
) -> tuple[RetryRequestPlan, ...]:
    diagnostic_indexes = {
        diagnostics.candidate_id: index
        for index, diagnostics in enumerate(candidate_diagnostics)
    }
    speech = _speech_intervals(audio_analysis)
    facts: list[RetryCandidateFacts] = []
    for index, segment in enumerate(kept_segments):
        diagnostic_index = diagnostic_indexes[segment.candidate_id]
        diagnostics = candidate_diagnostics[diagnostic_index]
        gap_after_samples = 0
        gap_after_speech_coverage: float | None = None
        if index + 1 < len(kept_segments):
            next_diagnostics = candidate_diagnostics[
                diagnostic_indexes[kept_segments[index + 1].candidate_id]
            ]
            gap_after_samples = max(
                0,
                next_diagnostics.interval.start_sample
                - diagnostics.interval.end_sample,
            )
            if gap_after_samples and speech is not None:
                gap = AudioInterval(
                    start_sample=diagnostics.interval.end_sample,
                    end_sample=next_diagnostics.interval.start_sample,
                )
                gap_after_speech_coverage = interval_coverage_ratio(gap, speech)
        diagnostics = diagnostics.model_copy(
            update={
                "gap_after_samples": gap_after_samples,
                "gap_after_speech_coverage": gap_after_speech_coverage,
            }
        )
        candidate_diagnostics[diagnostic_index] = diagnostics
        facts.append(RetryCandidateFacts.from_diagnostics(diagnostics))

    if not enabled:
        return ()
    assessments, plans = plan_retry_requests(
        facts,
        total_samples=total_samples,
        sample_rate=_SAMPLE_RATE,
    )
    for segment, assessment in zip(kept_segments, assessments, strict=True):
        if not assessment.should_retry:
            continue
        diagnostic_index = diagnostic_indexes[segment.candidate_id]
        candidate_diagnostics[diagnostic_index] = candidate_diagnostics[
            diagnostic_index
        ].model_copy(
            update={
                "retry_candidate": True,
                "retry_reasons": assessment.reasons,
            }
        )
    return plans


def _window_index_for_time(
    value: float,
    windows: Sequence[_ChunkWindow],
) -> int:
    for window in windows:
        if window.core_start <= value < window.core_end:
            return window.index
    return windows[-1].index


def _score_segments(
    segments: Sequence[_ChunkSegment],
    diagnostics_by_id: Mapping[str, ASRSegmentDiagnostics],
    *,
    speech_intervals: Sequence[AudioInterval] | None,
):
    quality_facts: list[CandidateQualityFacts] = []
    for segment in segments:
        diagnostics = diagnostics_by_id[segment.candidate_id]
        assessment = evaluate_retry_reasons(
            RetryCandidateFacts.from_diagnostics(diagnostics),
            sample_rate=_SAMPLE_RATE,
        )
        quality_facts.append(
            CandidateQualityFacts(
                interval=diagnostics.interval,
                avg_logprob=diagnostics.avg_logprob,
                no_speech_prob=diagnostics.no_speech_prob,
                compression_ratio=diagnostics.compression_ratio,
                word_count=diagnostics.word_count,
                valid_word_timestamp_count=diagnostics.valid_word_timestamp_count,
                text_repetition_score=diagnostics.text_repetition_score,
                vad_speech_coverage=diagnostics.vad_speech_coverage,
                reason_severity=intrinsic_reason_severity(assessment),
                text_character_count=len(_text_key(segment.text)),
            )
        )
    return score_candidate_bundle(
        quality_facts,
        speech_intervals=speech_intervals,
    )


def _apply_selective_retries(
    model: Any,
    audio: Any,
    initial_segments: Sequence[_ChunkSegment],
    candidate_diagnostics: list[ASRSegmentDiagnostics],
    plans: Sequence[RetryRequestPlan],
    *,
    windows: Sequence[_ChunkWindow],
    detected_language: str,
    settings: ASRSettings,
    serialized_hotwords: str | None,
    audio_analysis: ASRAudioAnalysis,
    duration_seconds: float,
    window_candidate_counts: list[int],
    on_progress: ASRProgress | None,
) -> tuple[list[_ChunkSegment], tuple[ASRRetryRequestDiagnostics, ...]]:
    selected = list(initial_segments)
    diagnostics_by_id = {
        diagnostics.candidate_id: diagnostics
        for diagnostics in candidate_diagnostics
    }
    speech = _speech_intervals(audio_analysis)
    retry_diagnostics: list[ASRRetryRequestDiagnostics] = []

    for request_index, plan in enumerate(plans):
        target_ids = set(plan.candidate_ids)
        initial = [item for item in selected if item.candidate_id in target_ids]
        initial_score = _score_segments(
            initial,
            diagnostics_by_id,
            speech_intervals=speech,
        )
        if initial_score is None:
            raise RuntimeError("二次识别计划未引用有效首轮候选")

        status: Literal[
            "selected_initial",
            "selected_retry",
            "selected_empty",
            "failed",
        ]
        retry_score_value: float | None = None
        retry_segment_count = 0
        try:
            iterator, _ = model.transcribe(
                audio[plan.context.start_sample : plan.context.end_sample],
                language=detected_language,
                task="transcribe",
                beam_size=min(20, max(8, settings.beam_size + 3)),
                patience=1.2,
                temperature=0.0,
                repetition_penalty=1.1,
                no_repeat_ngram_size=3,
                vad_filter=settings.vad_filter,
                word_timestamps=True,
                condition_on_previous_text=False,
                **(
                    {"hotwords": serialized_hotwords}
                    if serialized_hotwords is not None
                    else {}
                ),
            )
            raw_segments = list(iterator)
            retry_segments: list[_ChunkSegment] = []
            parsed_diagnostics: list[ASRSegmentDiagnostics] = []
            context_start = plan.context.start_sample / _SAMPLE_RATE
            context_end = plan.context.end_sample / _SAMPLE_RATE
            for raw_segment_index, raw_segment in enumerate(raw_segments):
                bounds = _segment_bounds(
                    raw_segment,
                    context_start=context_start,
                    duration_seconds=duration_seconds,
                )
                if bounds is None:
                    continue
                start, end = bounds
                start_sample = round(start * _SAMPLE_RATE)
                end_sample = round(end * _SAMPLE_RATE)
                if (
                    start_sample >= plan.core.end_sample
                    or end_sample <= plan.core.start_sample
                ):
                    continue
                owned_start = max(start, plan.core.start_sample / _SAMPLE_RATE)
                owned_end = min(end, plan.core.end_sample / _SAMPLE_RATE)
                window_index = _window_index_for_time(
                    (owned_start + owned_end) / 2,
                    windows,
                )
                parsed = _parse_candidate(
                    raw_segment,
                    candidate_id=(
                        "candidate-"
                        f"retry-{request_index:06d}-segment-{raw_segment_index:06d}"
                    ),
                    window_index=window_index,
                    start=start,
                    end=end,
                    context_start=context_start,
                    context_end=context_end,
                    duration_seconds=duration_seconds,
                    total_samples=len(audio),
                    audio_analysis=audio_analysis,
                )
                if parsed is None:
                    continue
                clipped = _clip_retry_candidate_to_core(
                    *parsed,
                    core=plan.core,
                    total_samples=len(audio),
                    audio_analysis=audio_analysis,
                )
                if clipped is None:
                    continue
                retry_segment, segment_diagnostics = clipped
                retry_segments.append(retry_segment)
                parsed_diagnostics.append(segment_diagnostics)

            retry_diagnostics_by_id = {
                diagnostics.candidate_id: diagnostics
                for diagnostics in parsed_diagnostics
            }
            retry_score = _score_segments(
                retry_segments,
                retry_diagnostics_by_id,
                speech_intervals=speech,
            )
            retry_score_value = (
                retry_score.quality_score if retry_score is not None else None
            )
            retry_segment_count = len(retry_segments)
            core_speech_samples = (
                covered_samples(plan.core, speech) if speech is not None else None
            )
            select_retry = should_select_retry(
                initial_score,
                retry_score,
                core_speech_samples=core_speech_samples,
                sample_rate=_SAMPLE_RATE,
            )
            coverage_improved = (
                retry_score is not None
                and meaningfully_improves_speech_coverage(
                    initial_score,
                    retry_score,
                    sample_rate=_SAMPLE_RATE,
                )
            )

            candidate_diagnostics.extend(parsed_diagnostics)
            diagnostics_by_id.update(retry_diagnostics_by_id)
            for diagnostics in parsed_diagnostics:
                window_candidate_counts[diagnostics.window_index] += 1
            if select_retry:
                selected = [
                    item for item in selected if item.candidate_id not in target_ids
                ]
                selected.extend(
                    retry_segments
                    if coverage_improved
                    else _preserve_equivalent_initial_timeline(
                        initial,
                        retry_segments,
                    )
                )
                status = "selected_retry" if retry_segments else "selected_empty"
            else:
                status = "selected_initial"
        except Exception:
            status = "failed"
            retry_score_value = None
            retry_segment_count = 0

        retry_diagnostics.append(
            ASRRetryRequestDiagnostics(
                request_id=plan.request_id,
                candidate_ids=plan.candidate_ids,
                core=plan.core,
                context=plan.context,
                reasons=plan.reasons,
                status=status,
                initial_score=initial_score.quality_score,
                retry_score=retry_score_value,
                retry_segment_count=retry_segment_count,
            )
        )
        if on_progress:
            on_progress(0.8 + (request_index + 1) / len(plans) * 0.2)

    return _deduplicate_boundary_segments(selected), tuple(retry_diagnostics)


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
            needs_audio_analysis = (
                settings.dynamic_chunking or settings.selective_retry
            )
            if needs_audio_analysis:
                try:
                    from faster_whisper.vad import VadOptions, get_speech_timestamps

                    audio_analysis = _analyze_vad(
                        audio,
                        get_speech_timestamps=get_speech_timestamps,
                        vad_options_type=VadOptions,
                    )
                except Exception:
                    audio_analysis = ASRAudioAnalysis.failed(
                        sample_rate=_SAMPLE_RATE,
                        total_samples=len(audio),
                        vad_source="faster_whisper",
                    )
            else:
                audio_analysis = ASRAudioAnalysis.unavailable(
                    sample_rate=_SAMPLE_RATE,
                    total_samples=len(audio),
                    vad_source="disabled",
                )

            if settings.dynamic_chunking:
                window_strategy: Literal["fixed", "vad_dynamic"] = "vad_dynamic"
                if audio_analysis.vad_status == "available":
                    windows = _dynamic_chunk_windows(
                        len(audio),
                        audio_analysis.non_speech_intervals,
                    )
                else:
                    windows = _fixed_fallback_windows(len(audio))
            else:
                window_strategy = "fixed"
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

            serialized_hotwords = _serialize_hotwords(settings.hotwords)
            candidates: list[_ChunkSegment] = []
            candidate_diagnostics: list[ASRSegmentDiagnostics] = []
            window_candidate_counts = [0 for _ in windows]
            fallback_languages: list[str] = []
            fallback_probabilities: list[float] = []
            for window_number, window in enumerate(windows, start=1):
                iterator, info = model.transcribe(
                    audio[window.sample_start : window.sample_end],
                    language=detected_language,
                    task="transcribe",
                    beam_size=settings.beam_size,
                    vad_filter=settings.vad_filter,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                    **(
                        {"hotwords": serialized_hotwords}
                        if serialized_hotwords is not None
                        else {}
                    ),
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
                    bounds = _segment_bounds(
                        raw_segment,
                        context_start=window.context_start,
                        duration_seconds=duration,
                    )
                    if bounds is None:
                        continue
                    start, end = bounds
                    midpoint = (start + end) / 2
                    if not window.core_start <= midpoint < window.core_end:
                        continue
                    parsed = _parse_candidate(
                        raw_segment,
                        candidate_id=(
                            "candidate-"
                            f"chunk-{window.index:06d}-segment-{raw_segment_index:06d}"
                        ),
                        window_index=window.index,
                        start=start,
                        end=end,
                        context_start=window.context_start,
                        context_end=window.context_end,
                        duration_seconds=duration,
                        total_samples=len(audio),
                        audio_analysis=audio_analysis,
                    )
                    if parsed is None:
                        continue
                    candidate, segment_diagnostics = parsed
                    candidates.append(candidate)
                    candidate_diagnostics.append(segment_diagnostics)
                    window_candidate_counts[window.index] += 1
                if on_progress:
                    first_pass_span = 0.72 if settings.selective_retry else 0.92
                    on_progress(
                        0.08 + window_number / len(windows) * first_pass_span
                    )

            chunk_segments = _deduplicate_boundary_segments(candidates)
            if not chunk_segments:
                raise RuntimeError("没有从音频中识别出有效语音")
            if detected_language is None and fallback_languages:
                detected_language = Counter(fallback_languages).most_common(1)[0][0]
                detected_probability = max(fallback_probabilities, default=0.0)
            detected_language = detected_language or language
            retry_plans = _prepare_retry_plans(
                chunk_segments,
                candidate_diagnostics,
                audio_analysis=audio_analysis,
                total_samples=len(audio),
                enabled=settings.selective_retry,
            )
            if retry_plans:
                chunk_segments, retry_diagnostics = _apply_selective_retries(
                    model,
                    audio,
                    chunk_segments,
                    candidate_diagnostics,
                    retry_plans,
                    windows=windows,
                    detected_language=detected_language,
                    settings=settings,
                    serialized_hotwords=serialized_hotwords,
                    audio_analysis=audio_analysis,
                    duration_seconds=duration,
                    window_candidate_counts=window_candidate_counts,
                    on_progress=on_progress,
                )
            else:
                retry_diagnostics = ()
                if settings.selective_retry and on_progress:
                    on_progress(1.0)
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
            retry_reason_counts = Counter(
                reason
                for diagnostics in candidate_diagnostics
                if diagnostics.retry_candidate
                for reason in diagnostics.retry_reasons
            )
            diagnostics = ASRRunDiagnostics(
                window_strategy=window_strategy,
                audio=audio_analysis,
                windows=window_diagnostics,
                segments=tuple(candidate_diagnostics),
                retries=retry_diagnostics,
                summary=ASRDiagnosticsSummary(
                    window_count=len(window_diagnostics),
                    fallback_window_count=sum(
                        window.fallback_to_fixed for window in windows
                    ),
                    boundary_shift_abs_total_samples=sum(
                        abs(window.boundary_shift_samples) for window in windows
                    ),
                    candidate_segment_count=len(candidate_diagnostics),
                    deduplicated_segment_count=(
                        len(candidate_diagnostics) - len(chunk_segments)
                    ),
                    output_segment_count=len(segments),
                    retry_candidate_count=sum(
                        diagnostics.retry_candidate
                        for diagnostics in candidate_diagnostics
                    ),
                    retry_request_count=len(retry_diagnostics),
                    retry_selected_count=sum(
                        diagnostics.status in {"selected_retry", "selected_empty"}
                        for diagnostics in retry_diagnostics
                    ),
                    retry_initial_selected_count=sum(
                        diagnostics.status == "selected_initial"
                        for diagnostics in retry_diagnostics
                    ),
                    retry_failed_count=sum(
                        diagnostics.status == "failed"
                        for diagnostics in retry_diagnostics
                    ),
                    retry_reason_counts=dict(retry_reason_counts),
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
