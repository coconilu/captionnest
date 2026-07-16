from dataclasses import replace

import pytest

from sublingo_local.asr.diagnostics import AudioInterval
from sublingo_local.asr.retry import (
    ASRRetryPolicy,
    CandidateQualityFacts,
    RetryCandidateFacts,
    evaluate_retry_reasons,
    plan_retry_requests,
    score_candidate_bundle,
    should_select_retry,
    text_repetition_score,
)

SAMPLE_RATE = 16_000


def _facts(**updates: object) -> RetryCandidateFacts:
    value = RetryCandidateFacts(
        candidate_id="candidate-clear",
        interval=AudioInterval(start_sample=SAMPLE_RATE, end_sample=3 * SAMPLE_RATE),
        avg_logprob=-0.3,
        no_speech_prob=0.2,
        compression_ratio=1.4,
        word_count=4,
        valid_word_timestamp_count=4,
        text_repetition_score=0.0,
        vad_speech_coverage=0.7,
        gap_after_samples=0,
        gap_after_speech_coverage=None,
    )
    return replace(value, **updates)


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"avg_logprob": -1.6}, "low_avg_logprob"),
        ({"compression_ratio": 3.3}, "generation_repetition"),
        ({"text_repetition_score": 0.9}, "generation_repetition"),
        ({"no_speech_prob": 0.96}, "speech_conflict"),
        (
            {"no_speech_prob": 0.75, "vad_speech_coverage": 0.1},
            "speech_conflict",
        ),
        (
            {"word_count": 4, "valid_word_timestamp_count": 0},
            "word_timestamps_incomplete",
        ),
        (
            {
                "gap_after_samples": 2 * SAMPLE_RATE,
                "gap_after_speech_coverage": 0.6,
            },
            "speech_gap",
        ),
    ],
)
def test_each_hard_or_composite_reason_triggers_once(
    updates: dict[str, object],
    reason: str,
) -> None:
    assessment = evaluate_retry_reasons(
        _facts(**updates),
        sample_rate=SAMPLE_RATE,
    )

    assert assessment.should_retry is True
    assert assessment.reasons == (reason,)
    assert assessment.severity == 2


def test_single_weak_reason_does_not_trigger_but_two_families_do() -> None:
    low_confidence = evaluate_retry_reasons(
        _facts(avg_logprob=-0.9),
        sample_rate=SAMPLE_RATE,
    )
    combined = evaluate_retry_reasons(
        _facts(avg_logprob=-0.9, compression_ratio=2.5),
        sample_rate=SAMPLE_RATE,
    )

    assert low_confidence.reasons == ("low_avg_logprob",)
    assert low_confidence.should_retry is False
    assert combined.reasons == (
        "low_avg_logprob",
        "generation_repetition",
    )
    assert combined.should_retry is True


@pytest.mark.parametrize(
    "updates",
    [
        {"avg_logprob": -0.9},
        {"compression_ratio": 2.5},
        {"word_count": 4, "valid_word_timestamp_count": 3},
        {"compression_ratio": 2.5, "text_repetition_score": 0.7},
    ],
)
def test_each_single_weak_family_does_not_trigger(updates: dict[str, object]) -> None:
    assessment = evaluate_retry_reasons(
        _facts(**updates),
        sample_rate=SAMPLE_RATE,
    )

    assert assessment.evidence_family_count == 1
    assert assessment.severity == 1
    assert assessment.should_retry is False


@pytest.mark.parametrize(
    "updates",
    [
        {"avg_logprob": -0.9, "compression_ratio": 2.5},
        {
            "avg_logprob": -0.9,
            "word_count": 4,
            "valid_word_timestamp_count": 3,
        },
        {
            "compression_ratio": 2.5,
            "word_count": 4,
            "valid_word_timestamp_count": 3,
        },
    ],
)
def test_each_pair_of_weak_families_triggers(updates: dict[str, object]) -> None:
    assessment = evaluate_retry_reasons(
        _facts(**updates),
        sample_rate=SAMPLE_RATE,
    )

    assert assessment.evidence_family_count == 2
    assert assessment.severity == 2
    assert assessment.should_retry is True


def test_clear_candidate_and_raw_no_speech_signal_do_not_trigger() -> None:
    clear = evaluate_retry_reasons(_facts(), sample_rate=SAMPLE_RATE)
    raw_no_speech = evaluate_retry_reasons(
        _facts(no_speech_prob=0.8, vad_speech_coverage=0.5),
        sample_rate=SAMPLE_RATE,
    )

    assert clear.reasons == ()
    assert clear.should_retry is False
    assert raw_no_speech.reasons == ()
    assert raw_no_speech.should_retry is False


def test_repetition_score_is_language_independent_and_deterministic() -> None:
    assert text_repetition_score("清晰的普通字幕") == 0.0
    assert text_repetition_score("ha ha ha ha") == pytest.approx(1.0)
    assert text_repetition_score("哈哈哈哈哈哈") == pytest.approx(1.0)
    assert text_repetition_score("abcabcabc末尾") == pytest.approx(9 / 11)
    assert text_repetition_score("abcabcabc末尾") == text_repetition_score(
        "abcabcabc末尾"
    )


def test_planner_merges_overlapping_targets_and_applies_total_budget() -> None:
    candidates = [
        _facts(
            candidate_id="candidate-a",
            interval=AudioInterval(
                start_sample=1 * SAMPLE_RATE,
                end_sample=3 * SAMPLE_RATE,
            ),
            avg_logprob=-1.6,
        ),
        _facts(
            candidate_id="candidate-b",
            interval=AudioInterval(
                start_sample=4 * SAMPLE_RATE,
                end_sample=5 * SAMPLE_RATE,
            ),
            compression_ratio=3.3,
        ),
        _facts(
            candidate_id="candidate-c",
            interval=AudioInterval(
                start_sample=40 * SAMPLE_RATE,
                end_sample=42 * SAMPLE_RATE,
            ),
            avg_logprob=-1.6,
        ),
    ]

    assessments, plans = plan_retry_requests(
        candidates,
        total_samples=60 * SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )

    assert all(item.should_retry for item in assessments)
    assert len(plans) == 1
    assert plans[0].request_id == "retry-000000"
    assert plans[0].candidate_ids == ("candidate-a", "candidate-b")
    assert plans[0].core == AudioInterval(
        start_sample=1 * SAMPLE_RATE,
        end_sample=5 * SAMPLE_RATE,
    )
    assert plans[0].context == AudioInterval(
        start_sample=0,
        end_sample=12 * SAMPLE_RATE,
    )


def test_planner_does_not_merge_across_unmarked_candidate() -> None:
    candidates = [
        _facts(
            candidate_id="candidate-a",
            interval=AudioInterval(
                start_sample=1 * SAMPLE_RATE,
                end_sample=2 * SAMPLE_RATE,
            ),
            avg_logprob=-1.6,
        ),
        _facts(
            candidate_id="candidate-middle",
            interval=AudioInterval(
                start_sample=3 * SAMPLE_RATE,
                end_sample=4 * SAMPLE_RATE,
            ),
        ),
        _facts(
            candidate_id="candidate-b",
            interval=AudioInterval(
                start_sample=5 * SAMPLE_RATE,
                end_sample=6 * SAMPLE_RATE,
            ),
            avg_logprob=-1.6,
        ),
    ]

    _, plans = plan_retry_requests(
        candidates,
        total_samples=60 * SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )

    assert [plan.candidate_ids for plan in plans] == [
        ("candidate-a",),
        ("candidate-b",),
    ]
    assert [plan.core for plan in plans] == [
        AudioInterval(
            start_sample=1 * SAMPLE_RATE,
            end_sample=2 * SAMPLE_RATE,
        ),
        AudioInterval(
            start_sample=5 * SAMPLE_RATE,
            end_sample=6 * SAMPLE_RATE,
        ),
    ]


def test_planner_keeps_candidate_flag_but_skips_overlong_request() -> None:
    candidate = _facts(
        interval=AudioInterval(start_sample=1, end_sample=25 * SAMPLE_RATE),
        avg_logprob=-1.6,
    )

    assessments, plans = plan_retry_requests(
        [candidate],
        total_samples=30 * SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )

    assert assessments[0].should_retry is True
    assert plans == ()


def test_planner_never_reprocesses_the_entire_audio() -> None:
    assessments, plans = plan_retry_requests(
        [_facts(avg_logprob=-1.6)],
        total_samples=4 * SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )

    assert assessments[0].should_retry is True
    assert plans == ()


def test_planner_does_not_mass_retry_clear_audio() -> None:
    clear_candidates = [
        _facts(
            candidate_id=f"candidate-clear-{index:03d}",
            interval=AudioInterval(
                start_sample=(index * 2 + 1) * SAMPLE_RATE,
                end_sample=(index * 2 + 2) * SAMPLE_RATE,
            ),
        )
        for index in range(100)
    ]

    assessments, plans = plan_retry_requests(
        clear_candidates,
        total_samples=202 * SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )

    assert all(not assessment.should_retry for assessment in assessments)
    assert plans == ()


def _quality(
    *,
    interval: AudioInterval | None = None,
    reason_severity: int,
    avg_logprob: float = -0.3,
) -> CandidateQualityFacts:
    return CandidateQualityFacts(
        interval=interval or AudioInterval(start_sample=0, end_sample=SAMPLE_RATE),
        avg_logprob=avg_logprob,
        no_speech_prob=0.2,
        compression_ratio=1.3,
        word_count=2,
        valid_word_timestamp_count=2,
        text_repetition_score=0.0,
        vad_speech_coverage=0.8,
        reason_severity=reason_severity,
        text_character_count=4,
    )


def test_retry_selection_requires_strict_quality_improvement_without_new_gap() -> None:
    speech = (AudioInterval(start_sample=0, end_sample=SAMPLE_RATE),)
    initial = score_candidate_bundle(
        [_quality(reason_severity=2, avg_logprob=-1.0)],
        speech_intervals=speech,
    )
    improved = score_candidate_bundle(
        [_quality(reason_severity=0, avg_logprob=-0.2)],
        speech_intervals=speech,
    )
    tied = score_candidate_bundle(
        [_quality(reason_severity=2, avg_logprob=-1.0)],
        speech_intervals=speech,
    )
    shorter = score_candidate_bundle(
        [
            _quality(
                interval=AudioInterval(
                    start_sample=0,
                    end_sample=SAMPLE_RATE // 2,
                ),
                reason_severity=0,
                avg_logprob=-0.2,
            )
        ],
        speech_intervals=speech,
    )

    assert initial is not None
    assert should_select_retry(
        initial,
        improved,
        core_speech_samples=SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )
    assert not should_select_retry(
        initial,
        tied,
        core_speech_samples=SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )
    assert not should_select_retry(
        initial,
        shorter,
        core_speech_samples=SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
    )


def test_retry_score_is_invariant_to_equivalent_segment_splitting() -> None:
    initial = score_candidate_bundle(
        [
            replace(
                _quality(reason_severity=2),
                interval=AudioInterval(
                    start_sample=0,
                    end_sample=SAMPLE_RATE // 2,
                ),
                text_character_count=2,
            ),
            replace(
                _quality(reason_severity=2),
                interval=AudioInterval(
                    start_sample=SAMPLE_RATE // 2,
                    end_sample=SAMPLE_RATE,
                ),
                text_character_count=2,
            ),
        ],
        speech_intervals=None,
    )
    retry = score_candidate_bundle(
        [_quality(reason_severity=2)],
        speech_intervals=None,
    )

    assert initial is not None
    assert retry is not None
    assert initial.reason_severity == retry.reason_severity == 2
    assert initial.quality_score == pytest.approx(retry.quality_score)
    assert not should_select_retry(
        initial,
        retry,
        core_speech_samples=None,
        sample_rate=SAMPLE_RATE,
    )


def test_retry_rejects_equal_total_coverage_moved_to_another_interval() -> None:
    initial = score_candidate_bundle(
        [
            _quality(
                interval=AudioInterval(
                    start_sample=5 * SAMPLE_RATE,
                    end_sample=6 * SAMPLE_RATE,
                ),
                reason_severity=2,
                avg_logprob=-1.0,
            ),
            _quality(
                interval=AudioInterval(
                    start_sample=7 * SAMPLE_RATE,
                    end_sample=8 * SAMPLE_RATE,
                ),
                reason_severity=2,
                avg_logprob=-1.0,
            ),
        ],
        speech_intervals=None,
    )
    moved = score_candidate_bundle(
        [
            _quality(
                interval=AudioInterval(
                    start_sample=5 * SAMPLE_RATE,
                    end_sample=7 * SAMPLE_RATE,
                ),
                reason_severity=0,
                avg_logprob=-0.1,
            )
        ],
        speech_intervals=None,
    )

    assert initial is not None
    assert not should_select_retry(
        initial,
        moved,
        core_speech_samples=None,
        sample_rate=SAMPLE_RATE,
    )


def test_retry_allows_only_explicit_thirty_millisecond_boundary_drift() -> None:
    initial = score_candidate_bundle(
        [
            _quality(
                interval=AudioInterval(start_sample=10_000, end_sample=20_000),
                reason_severity=2,
                avg_logprob=-1.0,
            )
        ],
        speech_intervals=None,
    )
    within_tolerance = score_candidate_bundle(
        [
            _quality(
                interval=AudioInterval(start_sample=9_520, end_sample=19_520),
                reason_severity=0,
                avg_logprob=-0.1,
            )
        ],
        speech_intervals=None,
    )
    outside_tolerance = score_candidate_bundle(
        [
            _quality(
                interval=AudioInterval(start_sample=9_519, end_sample=19_519),
                reason_severity=0,
                avg_logprob=-0.1,
            )
        ],
        speech_intervals=None,
    )

    assert initial is not None
    assert should_select_retry(
        initial,
        within_tolerance,
        core_speech_samples=None,
        sample_rate=SAMPLE_RATE,
    )
    assert not should_select_retry(
        initial,
        outside_tolerance,
        core_speech_samples=None,
        sample_rate=SAMPLE_RATE,
    )


def test_empty_retry_only_removes_vad_confirmed_silence_hallucination() -> None:
    initial = score_candidate_bundle(
        [_quality(reason_severity=2)],
        speech_intervals=(),
    )

    assert initial is not None
    assert should_select_retry(
        initial,
        None,
        core_speech_samples=0,
        sample_rate=SAMPLE_RATE,
    )
    assert not should_select_retry(
        initial,
        None,
        core_speech_samples=1,
        sample_rate=SAMPLE_RATE,
    )
    assert not should_select_retry(
        initial,
        None,
        core_speech_samples=None,
        sample_rate=SAMPLE_RATE,
    )


def test_policy_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="比例"):
        ASRRetryPolicy(max_total_audio_ratio=1.1)
    with pytest.raises(ValueError, match="容纳"):
        ASRRetryPolicy(max_request_seconds=20, max_core_seconds=20)
    with pytest.raises(ValueError, match="0.03"):
        ASRRetryPolicy(coverage_tolerance_seconds=0.031)
