from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .diagnostics import AudioInterval


@dataclass(frozen=True)
class TimestampNormalizationPolicy:
    """Conservative, model-independent timestamp adjustment limits."""

    max_boundary_shift_ms: int = 300
    min_word_duration_ms: int = 100
    min_segment_duration_ms: int = 100
    small_gap_ms: int = 120
    min_silence_ms: int = 120

    def __post_init__(self) -> None:
        values = (
            self.max_boundary_shift_ms,
            self.min_word_duration_ms,
            self.min_segment_duration_ms,
            self.small_gap_ms,
            self.min_silence_ms,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        ):
            raise ValueError("时间戳规范化阈值必须为正整数")


DEFAULT_TIMESTAMP_NORMALIZATION_POLICY = TimestampNormalizationPolicy()


@dataclass(frozen=True)
class TimestampTrackItem:
    interval: AudioInterval
    words: tuple[AudioInterval, ...] = ()


@dataclass(frozen=True)
class TimestampNormalizationStats:
    word_boundary_shift_count: int = 0
    segment_boundary_shift_count: int = 0
    boundary_shift_abs_total_samples: int = 0
    unsafe_adjustment_count: int = 0
    fallback_to_original_count: int = 0


@dataclass(frozen=True)
class TimestampNormalizationResult:
    items: tuple[TimestampTrackItem, ...]
    stats: TimestampNormalizationStats
    eligible_non_speech_intervals: tuple[AudioInterval, ...] = ()


def _milliseconds_to_samples(value: int, sample_rate: int) -> int:
    return max(1, round(value * sample_rate / 1_000))


def _containing_start_silence(
    boundary: int,
    silences: Sequence[AudioInterval],
) -> AudioInterval | None:
    return next(
        (
            silence
            for silence in silences
            if silence.start_sample <= boundary < silence.end_sample
        ),
        None,
    )


def _containing_end_silence(
    boundary: int,
    silences: Sequence[AudioInterval],
) -> AudioInterval | None:
    return next(
        (
            silence
            for silence in silences
            if silence.start_sample < boundary <= silence.end_sample
        ),
        None,
    )


def _clip_interval_to_silence(
    interval: AudioInterval,
    *,
    silences: Sequence[AudioInterval],
    max_shift: int,
    min_duration: int,
    maximum_start: int | None = None,
    minimum_end: int | None = None,
) -> tuple[AudioInterval, int]:
    start = interval.start_sample
    end = interval.end_sample
    unsafe_count = 0

    start_silence = _containing_start_silence(start, silences)
    if start_silence is not None:
        candidate = start_silence.end_sample
        safe = (
            candidate - interval.start_sample <= max_shift
            and candidate <= end - min_duration
            and (maximum_start is None or candidate <= maximum_start)
        )
        if safe:
            start = candidate
        else:
            unsafe_count += 1

    end_silence = _containing_end_silence(end, silences)
    if end_silence is not None:
        candidate = end_silence.start_sample
        safe = (
            interval.end_sample - candidate <= max_shift
            and candidate >= start + min_duration
            and (minimum_end is None or candidate >= minimum_end)
        )
        if safe:
            end = candidate
        else:
            unsafe_count += 1

    return AudioInterval(start_sample=start, end_sample=end), unsafe_count


def _relevant_silences(
    left_end: int,
    right_start: int,
    silences: Sequence[AudioInterval],
) -> tuple[AudioInterval, ...]:
    boundary_start = min(left_end, right_start)
    boundary_end = max(left_end, right_start)
    if boundary_start == boundary_end:
        return ()
    return tuple(
        silence
        for silence in silences
        if max(boundary_start, silence.start_sample)
        < min(boundary_end, silence.end_sample)
    )


def _adjust_adjacent_intervals(
    intervals: Sequence[AudioInterval],
    *,
    originals: Sequence[AudioInterval],
    silences: Sequence[AudioInterval],
    max_shift: int,
    min_duration: int,
    small_gap: int,
    maximum_starts: Sequence[int | None] | None = None,
    minimum_ends: Sequence[int | None] | None = None,
) -> tuple[list[AudioInterval], int]:
    adjusted = list(intervals)
    unsafe_count = 0
    maximum_starts = maximum_starts or [None] * len(adjusted)
    minimum_ends = minimum_ends or [None] * len(adjusted)

    def safe_end(index: int, target: int) -> bool:
        current = adjusted[index]
        anchor = minimum_ends[index]
        return (
            target >= current.start_sample + min_duration
            and abs(target - originals[index].end_sample) <= max_shift
            and (anchor is None or target >= anchor)
        )

    def safe_start(index: int, target: int) -> bool:
        current = adjusted[index]
        anchor = maximum_starts[index]
        return (
            target <= current.end_sample - min_duration
            and abs(target - originals[index].start_sample) <= max_shift
            and (anchor is None or target <= anchor)
        )

    for index in range(len(adjusted) - 1):
        left = adjusted[index]
        right = adjusted[index + 1]
        relevant = _relevant_silences(
            left.end_sample,
            right.start_sample,
            silences,
        )
        candidates = [
            silence
            for silence in relevant
            if safe_end(index, silence.start_sample)
            and safe_start(index + 1, silence.end_sample)
        ]
        if candidates:
            selected = min(
                candidates,
                key=lambda silence: (
                    abs(silence.start_sample - left.end_sample)
                    + abs(silence.end_sample - right.start_sample),
                    -(silence.end_sample - silence.start_sample),
                    silence.start_sample,
                ),
            )
            adjusted[index] = AudioInterval(
                start_sample=left.start_sample,
                end_sample=selected.start_sample,
            )
            adjusted[index + 1] = AudioInterval(
                start_sample=selected.end_sample,
                end_sample=right.end_sample,
            )
            continue
        if relevant:
            unsafe_count += 1
            continue

        gap = right.start_sample - left.end_sample
        if gap == 0 or gap > small_gap:
            continue
        midpoint = (left.end_sample + right.start_sample) // 2
        if safe_end(index, midpoint) and safe_start(index + 1, midpoint):
            adjusted[index] = AudioInterval(
                start_sample=left.start_sample,
                end_sample=midpoint,
            )
            adjusted[index + 1] = AudioInterval(
                start_sample=midpoint,
                end_sample=right.end_sample,
            )
        else:
            unsafe_count += 1

    return adjusted, unsafe_count


def _track_is_safe(
    items: Sequence[TimestampTrackItem],
    *,
    originals: Sequence[TimestampTrackItem],
    total_samples: int,
    max_shift: int,
) -> bool:
    if len(items) != len(originals):
        return False

    previous_segment_start = -1
    previous_segment_end = -1
    previous_word_start = -1
    previous_word_end = -1
    for item, original in zip(items, originals, strict=True):
        interval = item.interval
        if not 0 <= interval.start_sample < interval.end_sample <= total_samples:
            return False
        if (
            abs(interval.start_sample - original.interval.start_sample) > max_shift
            or abs(interval.end_sample - original.interval.end_sample) > max_shift
            or interval.start_sample < previous_segment_start
            or interval.end_sample < previous_segment_end
            or len(item.words) != len(original.words)
        ):
            return False
        previous_segment_start = interval.start_sample
        previous_segment_end = interval.end_sample

        for word, original_word in zip(item.words, original.words, strict=True):
            if not (
                interval.start_sample
                <= word.start_sample
                < word.end_sample
                <= interval.end_sample
            ):
                return False
            if (
                abs(word.start_sample - original_word.start_sample) > max_shift
                or abs(word.end_sample - original_word.end_sample) > max_shift
                or word.start_sample < previous_word_start
                or word.end_sample < previous_word_end
            ):
                return False
            previous_word_start = word.start_sample
            previous_word_end = word.end_sample
    return True


def _stats(
    items: Sequence[TimestampTrackItem],
    originals: Sequence[TimestampTrackItem],
    *,
    unsafe_adjustment_count: int,
    fallback_to_original_count: int = 0,
) -> TimestampNormalizationStats:
    word_count = 0
    segment_count = 0
    total_shift = 0
    for item, original in zip(items, originals, strict=True):
        for current_value, original_value in (
            (item.interval.start_sample, original.interval.start_sample),
            (item.interval.end_sample, original.interval.end_sample),
        ):
            if current_value != original_value:
                segment_count += 1
                total_shift += abs(current_value - original_value)
        for word, original_word in zip(item.words, original.words, strict=True):
            for current_value, original_value in (
                (word.start_sample, original_word.start_sample),
                (word.end_sample, original_word.end_sample),
            ):
                if current_value != original_value:
                    word_count += 1
                    total_shift += abs(current_value - original_value)
    return TimestampNormalizationStats(
        word_boundary_shift_count=word_count,
        segment_boundary_shift_count=segment_count,
        boundary_shift_abs_total_samples=total_shift,
        unsafe_adjustment_count=unsafe_adjustment_count,
        fallback_to_original_count=fallback_to_original_count,
    )


def normalize_timestamp_track(
    items: Sequence[TimestampTrackItem],
    *,
    non_speech_intervals: Sequence[AudioInterval],
    sample_rate: int,
    total_samples: int,
    policy: TimestampNormalizationPolicy | None = None,
) -> TimestampNormalizationResult:
    """Normalize timestamp boundaries without changing track structure or order."""
    originals = tuple(items)
    if not originals or not non_speech_intervals:
        return TimestampNormalizationResult(
            items=originals,
            stats=TimestampNormalizationStats(),
        )
    if sample_rate <= 0 or total_samples <= 0:
        raise ValueError("时间戳规范化需要有效的采样率与媒体长度")

    policy = policy or DEFAULT_TIMESTAMP_NORMALIZATION_POLICY
    max_shift = _milliseconds_to_samples(
        policy.max_boundary_shift_ms,
        sample_rate,
    )
    min_word_duration = _milliseconds_to_samples(
        policy.min_word_duration_ms,
        sample_rate,
    )
    min_segment_duration = _milliseconds_to_samples(
        policy.min_segment_duration_ms,
        sample_rate,
    )
    small_gap = _milliseconds_to_samples(policy.small_gap_ms, sample_rate)
    min_silence = _milliseconds_to_samples(policy.min_silence_ms, sample_rate)
    silences = tuple(
        sorted(
            (
                silence
                for silence in non_speech_intervals
                if silence.end_sample - silence.start_sample >= min_silence
            ),
            key=lambda silence: (silence.start_sample, silence.end_sample),
        )
    )
    if not silences:
        return TimestampNormalizationResult(
            items=originals,
            stats=TimestampNormalizationStats(),
        )

    unsafe_count = 0
    word_counts = [len(item.words) for item in originals]
    original_words = [word for item in originals for word in item.words]
    adjusted_words: list[AudioInterval] = []
    for word in original_words:
        adjusted, unsafe = _clip_interval_to_silence(
            word,
            silences=silences,
            max_shift=max_shift,
            min_duration=min_word_duration,
        )
        adjusted_words.append(adjusted)
        unsafe_count += unsafe
    adjusted_words, unsafe = _adjust_adjacent_intervals(
        adjusted_words,
        originals=original_words,
        silences=silences,
        max_shift=max_shift,
        min_duration=min_word_duration,
        small_gap=small_gap,
    )
    unsafe_count += unsafe

    words_by_item: list[tuple[AudioInterval, ...]] = []
    cursor = 0
    for count in word_counts:
        words_by_item.append(tuple(adjusted_words[cursor : cursor + count]))
        cursor += count

    original_segments = [item.interval for item in originals]
    adjusted_segments: list[AudioInterval] = []
    maximum_starts: list[int | None] = []
    minimum_ends: list[int | None] = []
    for interval, words in zip(original_segments, words_by_item, strict=True):
        maximum_start = words[0].start_sample if words else None
        minimum_end = words[-1].end_sample if words else None
        adjusted, unsafe = _clip_interval_to_silence(
            interval,
            silences=silences,
            max_shift=max_shift,
            min_duration=min_segment_duration,
            maximum_start=maximum_start,
            minimum_end=minimum_end,
        )
        adjusted_segments.append(adjusted)
        maximum_starts.append(maximum_start)
        minimum_ends.append(minimum_end)
        unsafe_count += unsafe
    adjusted_segments, unsafe = _adjust_adjacent_intervals(
        adjusted_segments,
        originals=original_segments,
        silences=silences,
        max_shift=max_shift,
        min_duration=min_segment_duration,
        small_gap=small_gap,
        maximum_starts=maximum_starts,
        minimum_ends=minimum_ends,
    )
    unsafe_count += unsafe

    normalized = tuple(
        TimestampTrackItem(interval=interval, words=words)
        for interval, words in zip(
            adjusted_segments,
            words_by_item,
            strict=True,
        )
    )
    if not _track_is_safe(
        normalized,
        originals=originals,
        total_samples=total_samples,
        max_shift=max_shift,
    ):
        return TimestampNormalizationResult(
            items=originals,
            stats=_stats(
                originals,
                originals,
                unsafe_adjustment_count=unsafe_count + 1,
                fallback_to_original_count=1,
            ),
            eligible_non_speech_intervals=silences,
        )
    return TimestampNormalizationResult(
        items=normalized,
        stats=_stats(
            normalized,
            originals,
            unsafe_adjustment_count=unsafe_count,
        ),
        eligible_non_speech_intervals=silences,
    )
