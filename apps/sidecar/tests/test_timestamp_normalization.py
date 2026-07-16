from sublingo_local.asr.diagnostics import AudioInterval
from sublingo_local.asr.timestamp_normalization import (
    TimestampNormalizationPolicy,
    TimestampNormalizationStats,
    TimestampTrackItem,
    normalize_timestamp_track,
)

SAMPLE_RATE = 1_000
TOTAL_SAMPLES = 5_000


def _interval(start: int, end: int) -> AudioInterval:
    return AudioInterval(start_sample=start, end_sample=end)


def _normalize(
    items: list[TimestampTrackItem],
    silences: list[AudioInterval],
):
    return normalize_timestamp_track(
        items,
        non_speech_intervals=silences,
        sample_rate=SAMPLE_RATE,
        total_samples=TOTAL_SAMPLES,
    )


def test_silence_suppression_clips_word_and_parent_boundaries() -> None:
    item = TimestampTrackItem(
        interval=_interval(900, 3_100),
        words=(
            _interval(900, 1_600),
            _interval(2_400, 3_100),
        ),
    )

    result = _normalize(
        [item],
        [
            _interval(0, 1_000),
            _interval(1_500, 2_500),
            _interval(3_000, 5_000),
        ],
    )

    assert result.items == (
        TimestampTrackItem(
            interval=_interval(1_000, 3_000),
            words=(
                _interval(1_000, 1_500),
                _interval(2_500, 3_000),
            ),
        ),
    )
    assert result.stats.word_boundary_shift_count == 4
    assert result.stats.segment_boundary_shift_count == 2
    assert result.stats.boundary_shift_abs_total_samples == 600


def test_overlap_and_small_gap_use_deterministic_midpoint_without_nearby_silence() -> None:
    overlap_items = [
        TimestampTrackItem(interval=_interval(100, 1_100)),
        TimestampTrackItem(interval=_interval(1_000, 2_000)),
    ]
    small_gap_items = [
        TimestampTrackItem(interval=_interval(100, 1_000)),
        TimestampTrackItem(interval=_interval(1_080, 2_000)),
    ]
    unrelated_silence = [_interval(4_000, 4_500)]

    overlap = _normalize(overlap_items, unrelated_silence)
    small_gap = _normalize(small_gap_items, unrelated_silence)

    assert [item.interval for item in overlap.items] == [
        _interval(100, 1_050),
        _interval(1_050, 2_000),
    ]
    assert [item.interval for item in small_gap.items] == [
        _interval(100, 1_040),
        _interval(1_040, 2_000),
    ]
    assert overlap == _normalize(overlap_items, unrelated_silence)
    assert small_gap == _normalize(small_gap_items, unrelated_silence)


def test_long_gap_prefers_silence_edges() -> None:
    items = [
        TimestampTrackItem(interval=_interval(100, 1_300)),
        TimestampTrackItem(interval=_interval(2_700, 3_800)),
    ]

    result = _normalize(items, [_interval(1_500, 2_500)])

    assert [item.interval for item in result.items] == [
        _interval(100, 1_500),
        _interval(2_500, 3_800),
    ]


def test_unsafe_long_gap_adjustment_keeps_original_boundaries() -> None:
    items = [
        TimestampTrackItem(interval=_interval(100, 1_000)),
        TimestampTrackItem(interval=_interval(3_000, 3_800)),
    ]

    result = _normalize(items, [_interval(1_500, 2_500)])

    assert result.items == tuple(items)
    assert result.stats.unsafe_adjustment_count == 1


def test_media_edges_are_clipped_without_exceeding_limits() -> None:
    item = TimestampTrackItem(interval=_interval(100, 1_950))

    result = normalize_timestamp_track(
        [item],
        non_speech_intervals=[_interval(0, 150), _interval(1_850, 2_000)],
        sample_rate=SAMPLE_RATE,
        total_samples=2_000,
    )

    assert result.items[0].interval == _interval(150, 1_850)


def test_no_silence_data_is_exact_noop() -> None:
    items = [
        TimestampTrackItem(
            interval=_interval(100, 2_000),
            words=(_interval(200, 900), _interval(1_000, 1_800)),
        )
    ]

    result = _normalize(items, [])

    assert result.items == tuple(items)
    assert result.stats.word_boundary_shift_count == 0
    assert result.stats.segment_boundary_shift_count == 0
    assert result.stats.boundary_shift_abs_total_samples == 0
    assert result.eligible_non_speech_intervals == ()


def test_subthreshold_silence_is_exact_noop() -> None:
    item = TimestampTrackItem(interval=_interval(100, 1_000))

    result = _normalize([item], [_interval(90, 200)])

    assert result.items == (item,)
    assert result.stats == TimestampNormalizationStats()
    assert result.eligible_non_speech_intervals == ()


def test_silence_input_order_does_not_change_result() -> None:
    items = [
        TimestampTrackItem(interval=_interval(100, 1_300)),
        TimestampTrackItem(interval=_interval(2_700, 3_800)),
    ]
    ordered = [_interval(1_500, 2_500), _interval(4_000, 4_500)]

    assert _normalize(items, ordered) == _normalize(items, list(reversed(ordered)))


def test_maximum_shift_and_minimum_duration_are_hard_bounds() -> None:
    policy = TimestampNormalizationPolicy(
        max_boundary_shift_ms=200,
        min_word_duration_ms=200,
        min_segment_duration_ms=200,
    )
    item = TimestampTrackItem(
        interval=_interval(100, 600),
        words=(_interval(100, 350),),
    )

    result = normalize_timestamp_track(
        [item],
        non_speech_intervals=[_interval(0, 300)],
        sample_rate=SAMPLE_RATE,
        total_samples=TOTAL_SAMPLES,
        policy=policy,
    )

    assert result.items == (item,)
    assert result.stats.unsafe_adjustment_count >= 1


def test_original_short_word_and_parent_are_extended_deterministically() -> None:
    item = TimestampTrackItem(
        interval=_interval(100, 150),
        words=(_interval(100, 150),),
    )

    result = _normalize([item], [_interval(4_000, 4_500)])

    expected = TimestampTrackItem(
        interval=_interval(75, 175),
        words=(_interval(75, 175),),
    )
    assert result.items == (expected,)
    assert result == _normalize([item], [_interval(4_000, 4_500)])
    assert result.stats.word_boundary_shift_count == 2
    assert result.stats.segment_boundary_shift_count == 2
    assert result.stats.boundary_shift_abs_total_samples == 100
    assert result.stats.fallback_to_original_count == 0


def test_parent_expands_to_contain_repaired_edge_word() -> None:
    item = TimestampTrackItem(
        interval=_interval(100, 200),
        words=(_interval(180, 190),),
    )

    result = _normalize([item], [_interval(4_000, 4_500)])

    assert result.items == (
        TimestampTrackItem(
            interval=_interval(100, 235),
            words=(_interval(135, 235),),
        ),
    )
    assert result.stats.fallback_to_original_count == 0


def test_overlapping_short_items_propagate_monotonic_repairs_forward() -> None:
    items = [
        TimestampTrackItem(
            interval=_interval(100, 150),
            words=(_interval(100, 150),),
        ),
        TimestampTrackItem(
            interval=_interval(110, 160),
            words=(_interval(110, 160),),
        ),
    ]

    result = _normalize(items, [_interval(4_000, 4_500)])

    assert result.items == (
        TimestampTrackItem(
            interval=_interval(75, 175),
            words=(_interval(75, 175),),
        ),
        TimestampTrackItem(
            interval=_interval(85, 185),
            words=(_interval(85, 185),),
        ),
    )
    assert result.stats.fallback_to_original_count == 0


def test_short_track_that_cannot_fit_minimum_duration_falls_back() -> None:
    item = TimestampTrackItem(
        interval=_interval(10, 60),
        words=(_interval(10, 60),),
    )

    result = normalize_timestamp_track(
        [item],
        non_speech_intervals=[],
        sample_rate=SAMPLE_RATE,
        total_samples=80,
    )

    assert result.items == (item,)
    assert result.stats.unsafe_adjustment_count == 1
    assert result.stats.fallback_to_original_count == 1
    assert result.stats.word_boundary_shift_count == 0
    assert result.stats.segment_boundary_shift_count == 0


def test_unsafe_final_structure_falls_back_to_exact_original_track() -> None:
    item = TimestampTrackItem(
        interval=_interval(100, 1_000),
        words=(_interval(50, 500),),
    )

    result = _normalize([item], [_interval(4_000, 4_500)])

    assert result.items == (item,)
    assert result.stats.fallback_to_original_count == 1
    assert result.stats.word_boundary_shift_count == 0
    assert result.stats.segment_boundary_shift_count == 0


def test_invalid_policy_is_rejected() -> None:
    invalid_values = (0, True, 1.5)
    for value in invalid_values:
        try:
            TimestampNormalizationPolicy(  # type: ignore[arg-type]
                max_boundary_shift_ms=value,
            )
        except ValueError as exc:
            assert "正整数" in str(exc)
        else:
            raise AssertionError("expected invalid policy to be rejected")
