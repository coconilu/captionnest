from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .diagnostics import ASRRetryReason as RetryReason
from .diagnostics import ASRSegmentDiagnostics, AudioInterval

RETRY_REASON_ORDER: tuple[RetryReason, ...] = (
    "low_avg_logprob",
    "generation_repetition",
    "speech_conflict",
    "word_timestamps_incomplete",
    "speech_gap",
)

_COMPACT_TEXT_PATTERN = re.compile(r"[^\w]+")


@dataclass(frozen=True)
class ASRRetryPolicy:
    avg_logprob_soft: float = -0.8
    avg_logprob_hard: float = -1.5
    compression_ratio_soft: float = 2.4
    compression_ratio_hard: float = 3.2
    repetition_soft: float = 0.6
    repetition_hard: float = 0.85
    no_speech_conflict: float = 0.7
    no_speech_hard: float = 0.95
    conflict_vad_coverage_max: float = 0.15
    word_coverage_soft: float = 0.8
    word_coverage_hard: float = 0.2
    speech_gap_seconds: float = 2.0
    speech_gap_coverage_min: float = 0.6
    context_seconds: float = 2.0
    max_core_seconds: float = 24.0
    max_request_seconds: float = 28.0
    max_requests: int = 12
    max_total_audio_ratio: float = 0.2
    min_total_audio_seconds: float = 10.0
    max_total_audio_seconds: float = 120.0
    selection_margin: float = 0.1
    coverage_tolerance_seconds: float = 0.03

    def __post_init__(self) -> None:
        numeric_values = (
            self.avg_logprob_soft,
            self.avg_logprob_hard,
            self.compression_ratio_soft,
            self.compression_ratio_hard,
            self.repetition_soft,
            self.repetition_hard,
            self.no_speech_conflict,
            self.no_speech_hard,
            self.conflict_vad_coverage_max,
            self.word_coverage_soft,
            self.word_coverage_hard,
            self.speech_gap_seconds,
            self.speech_gap_coverage_min,
            self.context_seconds,
            self.max_core_seconds,
            self.max_request_seconds,
            self.max_total_audio_ratio,
            self.min_total_audio_seconds,
            self.max_total_audio_seconds,
            self.selection_margin,
            self.coverage_tolerance_seconds,
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("重试策略只能包含有限数值")
        if self.avg_logprob_hard > self.avg_logprob_soft:
            raise ValueError("平均对数概率硬阈值不能高于软阈值")
        if self.compression_ratio_hard < self.compression_ratio_soft:
            raise ValueError("压缩率硬阈值不能低于软阈值")
        if not 0 <= self.repetition_soft <= self.repetition_hard <= 1:
            raise ValueError("重复度阈值必须在 [0, 1] 内且保持递增")
        if not 0 <= self.no_speech_conflict <= self.no_speech_hard <= 1:
            raise ValueError("无语音概率阈值必须在 [0, 1] 内且保持递增")
        if not 0 <= self.conflict_vad_coverage_max <= 1:
            raise ValueError("VAD 冲突覆盖率阈值必须在 [0, 1] 内")
        if not 0 <= self.word_coverage_hard <= self.word_coverage_soft <= 1:
            raise ValueError("词时间戳覆盖率阈值必须在 [0, 1] 内且保持递增")
        if not 0 <= self.speech_gap_coverage_min <= 1:
            raise ValueError("空洞语音覆盖率阈值必须在 [0, 1] 内")
        if not 0 < self.max_total_audio_ratio <= 1:
            raise ValueError("重试总音频比例必须在 (0, 1] 内")
        if self.max_requests <= 0:
            raise ValueError("最大重试请求数必须大于 0")
        if self.context_seconds < 0:
            raise ValueError("重试上下文不能为负数")
        if (
            self.speech_gap_seconds <= 0
            or self.max_core_seconds <= 0
            or self.max_request_seconds <= 0
        ):
            raise ValueError("重试区间上限必须大于 0")
        if (
            self.min_total_audio_seconds <= 0
            or self.max_total_audio_seconds < self.min_total_audio_seconds
        ):
            raise ValueError("重试总时长上下限必须为正且保持递增")
        if self.selection_margin < 0:
            raise ValueError("二次结果择优边际不能为负数")
        if not 0 <= self.coverage_tolerance_seconds <= 0.03:
            raise ValueError("覆盖区间容差必须在 [0, 0.03] 秒内")
        if self.max_core_seconds + 2 * self.context_seconds > self.max_request_seconds:
            raise ValueError("单请求上限必须容纳核心和两侧上下文")


DEFAULT_RETRY_POLICY = ASRRetryPolicy()


@dataclass(frozen=True)
class RetryCandidateFacts:
    candidate_id: str
    interval: AudioInterval
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None
    word_count: int
    valid_word_timestamp_count: int
    text_repetition_score: float
    vad_speech_coverage: float | None
    gap_after_samples: int
    gap_after_speech_coverage: float | None

    @classmethod
    def from_diagnostics(
        cls,
        diagnostics: ASRSegmentDiagnostics,
    ) -> RetryCandidateFacts:
        return cls(
            candidate_id=diagnostics.candidate_id,
            interval=diagnostics.interval,
            avg_logprob=diagnostics.avg_logprob,
            no_speech_prob=diagnostics.no_speech_prob,
            compression_ratio=diagnostics.compression_ratio,
            word_count=diagnostics.word_count,
            valid_word_timestamp_count=diagnostics.valid_word_timestamp_count,
            text_repetition_score=diagnostics.text_repetition_score,
            vad_speech_coverage=diagnostics.vad_speech_coverage,
            gap_after_samples=diagnostics.gap_after_samples,
            gap_after_speech_coverage=diagnostics.gap_after_speech_coverage,
        )


@dataclass(frozen=True)
class RetryAssessment:
    reasons: tuple[RetryReason, ...]
    evidence_family_count: int
    severity: int
    should_retry: bool


@dataclass(frozen=True)
class RetryRequestPlan:
    request_id: str
    candidate_ids: tuple[str, ...]
    core: AudioInterval
    context: AudioInterval
    reasons: tuple[RetryReason, ...]
    severity: int


@dataclass(frozen=True)
class CandidateQualityFacts:
    interval: AudioInterval
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None
    word_count: int
    valid_word_timestamp_count: int
    text_repetition_score: float
    vad_speech_coverage: float | None
    reason_severity: int
    text_character_count: int


@dataclass(frozen=True)
class CandidateBundleScore:
    quality_score: float
    reason_severity: int
    timeline_intervals: tuple[AudioInterval, ...]
    timeline_covered_samples: int
    speech_covered_intervals: tuple[AudioInterval, ...] | None
    speech_covered_samples: int | None
    word_timestamp_coverage: float
    text_character_count: int


def text_repetition_score(value: str) -> float:
    """Return the largest consecutive repeated n-gram share without retaining text."""

    compact = _COMPACT_TEXT_PATTERN.sub("", value.casefold())
    if len(compact) < 6:
        return 0.0
    best = 0.0
    for unit_size in range(1, min(12, len(compact) // 3) + 1):
        for start in range(0, len(compact) - unit_size * 3 + 1):
            unit = compact[start : start + unit_size]
            repeats = 1
            cursor = start + unit_size
            while compact[cursor : cursor + unit_size] == unit:
                repeats += 1
                cursor += unit_size
            if repeats >= 3:
                best = max(best, repeats * unit_size / len(compact))
    return min(1.0, best)


def covered_samples(
    interval: AudioInterval,
    coverage: Sequence[AudioInterval],
) -> int:
    return sum(
        max(
            0,
            min(interval.end_sample, item.end_sample)
            - max(interval.start_sample, item.start_sample),
        )
        for item in coverage
    )


def interval_coverage_ratio(
    interval: AudioInterval,
    coverage: Sequence[AudioInterval],
) -> float:
    duration = interval.end_sample - interval.start_sample
    return covered_samples(interval, coverage) / duration


def evaluate_retry_reasons(
    facts: RetryCandidateFacts,
    *,
    sample_rate: int,
    policy: ASRRetryPolicy = DEFAULT_RETRY_POLICY,
) -> RetryAssessment:
    strengths: dict[RetryReason, int] = {}

    if facts.avg_logprob is not None and facts.avg_logprob <= policy.avg_logprob_soft:
        strengths["low_avg_logprob"] = (
            2 if facts.avg_logprob <= policy.avg_logprob_hard else 1
        )

    generation_strength = 0
    if (
        facts.compression_ratio is not None
        and facts.compression_ratio >= policy.compression_ratio_soft
    ):
        generation_strength = max(
            generation_strength,
            2
            if facts.compression_ratio >= policy.compression_ratio_hard
            else 1,
        )
    if facts.text_repetition_score >= policy.repetition_soft:
        generation_strength = max(
            generation_strength,
            2 if facts.text_repetition_score >= policy.repetition_hard else 1,
        )
    if generation_strength:
        strengths["generation_repetition"] = generation_strength

    if facts.no_speech_prob is not None and (
        facts.no_speech_prob >= policy.no_speech_hard
        or (
            facts.no_speech_prob >= policy.no_speech_conflict
            and facts.vad_speech_coverage is not None
            and facts.vad_speech_coverage < policy.conflict_vad_coverage_max
        )
    ):
        strengths["speech_conflict"] = 2

    word_coverage = (
        facts.valid_word_timestamp_count / facts.word_count
        if facts.word_count
        else 0.0
    )
    if word_coverage < policy.word_coverage_soft:
        strengths["word_timestamps_incomplete"] = (
            2 if word_coverage <= policy.word_coverage_hard else 1
        )

    if (
        facts.gap_after_samples >= round(policy.speech_gap_seconds * sample_rate)
        and facts.gap_after_speech_coverage is not None
        and facts.gap_after_speech_coverage >= policy.speech_gap_coverage_min
    ):
        strengths["speech_gap"] = 2

    reasons = tuple(reason for reason in RETRY_REASON_ORDER if reason in strengths)
    severity = sum(strengths.values())
    should_retry = any(value >= 2 for value in strengths.values()) or len(strengths) >= 2
    return RetryAssessment(
        reasons=reasons,
        evidence_family_count=len(strengths),
        severity=severity,
        should_retry=should_retry,
    )


@dataclass(frozen=True)
class _PlanSeed:
    candidate_ids: tuple[str, ...]
    core_start: int
    core_end: int
    reasons: tuple[RetryReason, ...]
    severity: int


def _merge_plan_seeds(
    seeds: Sequence[_PlanSeed],
    *,
    blocked_intervals: Sequence[AudioInterval],
    total_samples: int,
    sample_rate: int,
    policy: ASRRetryPolicy,
) -> list[_PlanSeed]:
    context_samples = round(policy.context_seconds * sample_rate)
    max_core_samples = round(policy.max_core_seconds * sample_rate)
    max_request_samples = round(policy.max_request_seconds * sample_rate)
    merged: list[_PlanSeed] = []
    for seed in sorted(seeds, key=lambda item: (item.core_start, item.core_end)):
        if not merged:
            merged.append(seed)
            continue
        previous = merged[-1]
        previous_context_end = min(total_samples, previous.core_end + context_samples)
        seed_context_start = max(0, seed.core_start - context_samples)
        union_start = min(previous.core_start, seed.core_start)
        union_end = max(previous.core_end, seed.core_end)
        union_context_start = max(0, union_start - context_samples)
        union_context_end = min(total_samples, union_end + context_samples)
        contains_unmarked_candidate = any(
            blocked.start_sample < union_end
            and blocked.end_sample > union_start
            for blocked in blocked_intervals
        )
        if (
            seed_context_start <= previous_context_end
            and union_end - union_start <= max_core_samples
            and union_context_end - union_context_start <= max_request_samples
            and not contains_unmarked_candidate
        ):
            merged_reasons = tuple(
                reason
                for reason in RETRY_REASON_ORDER
                if reason in previous.reasons or reason in seed.reasons
            )
            merged[-1] = _PlanSeed(
                candidate_ids=previous.candidate_ids + seed.candidate_ids,
                core_start=union_start,
                core_end=union_end,
                reasons=merged_reasons,
                severity=previous.severity + seed.severity,
            )
        else:
            merged.append(seed)
    return merged


def plan_retry_requests(
    candidates: Sequence[RetryCandidateFacts],
    *,
    total_samples: int,
    sample_rate: int,
    policy: ASRRetryPolicy = DEFAULT_RETRY_POLICY,
) -> tuple[tuple[RetryAssessment, ...], tuple[RetryRequestPlan, ...]]:
    if total_samples <= 0 or sample_rate <= 0:
        raise ValueError("音频总采样数与采样率必须大于 0")

    assessments = tuple(
        evaluate_retry_reasons(item, sample_rate=sample_rate, policy=policy)
        for item in candidates
    )
    blocked_intervals = tuple(
        facts.interval
        for facts, assessment in zip(candidates, assessments, strict=True)
        if not assessment.should_retry
    )
    max_core_samples = round(policy.max_core_seconds * sample_rate)
    seeds: list[_PlanSeed] = []
    for facts, assessment in zip(candidates, assessments, strict=True):
        if not assessment.should_retry:
            continue
        core_end = facts.interval.end_sample
        if "speech_gap" in assessment.reasons:
            core_end = min(total_samples, core_end + facts.gap_after_samples)
        if core_end - facts.interval.start_sample > max_core_samples:
            continue
        if any(
            blocked.start_sample < core_end
            and blocked.end_sample > facts.interval.start_sample
            for blocked in blocked_intervals
        ):
            continue
        seeds.append(
            _PlanSeed(
                candidate_ids=(facts.candidate_id,),
                core_start=facts.interval.start_sample,
                core_end=core_end,
                reasons=assessment.reasons,
                severity=assessment.severity,
            )
        )

    merged = _merge_plan_seeds(
        seeds,
        blocked_intervals=blocked_intervals,
        total_samples=total_samples,
        sample_rate=sample_rate,
        policy=policy,
    )
    total_seconds = total_samples / sample_rate
    budget_samples = round(
        min(
            policy.max_total_audio_seconds,
            max(
                policy.min_total_audio_seconds,
                total_seconds * policy.max_total_audio_ratio,
            ),
        )
        * sample_rate
    )
    budget_samples = min(total_samples, budget_samples)
    context_samples = round(policy.context_seconds * sample_rate)
    max_request_samples = round(policy.max_request_seconds * sample_rate)
    selected: list[tuple[_PlanSeed, AudioInterval]] = []
    used_samples = 0
    for seed in sorted(merged, key=lambda item: (-item.severity, item.core_start)):
        context_start = max(0, seed.core_start - context_samples)
        context_end = min(total_samples, seed.core_end + context_samples)
        request_samples = context_end - context_start
        if context_start == 0 and context_end == total_samples:
            continue
        if (
            len(selected) >= policy.max_requests
            or used_samples + request_samples > budget_samples
        ):
            continue
        selected.append(
            (
                seed,
                AudioInterval(
                    start_sample=context_start,
                    end_sample=context_end,
                ),
            )
        )
        used_samples += request_samples

    # First reserve the minimum context for as many high-severity requests as fit.
    # Then spend the remaining bounded budget on wider context, deterministically.
    expanded: list[tuple[_PlanSeed, AudioInterval]] = []
    remaining_samples = budget_samples - used_samples
    for seed, minimum_context in selected:
        minimum_samples = minimum_context.end_sample - minimum_context.start_sample
        target_samples = min(
            max_request_samples,
            minimum_samples + remaining_samples,
            total_samples,
        )
        if target_samples == total_samples:
            # Never turn a selective retry into a second full-media transcription.
            target_samples = max(
                minimum_samples,
                total_samples - context_samples,
            )
        target_samples = max(minimum_samples, target_samples)
        center = (seed.core_start + seed.core_end) // 2
        context_start = center - target_samples // 2
        context_start = max(0, min(context_start, total_samples - target_samples))
        context_end = context_start + target_samples
        context_start = min(context_start, minimum_context.start_sample)
        context_end = max(context_end, minimum_context.end_sample)
        if context_end > total_samples:
            context_start -= context_end - total_samples
            context_end = total_samples
        actual_samples = context_end - context_start
        remaining_samples -= actual_samples - minimum_samples
        expanded.append(
            (
                seed,
                AudioInterval(
                    start_sample=context_start,
                    end_sample=context_end,
                ),
            )
        )

    plans: list[RetryRequestPlan] = []
    for index, (seed, context) in enumerate(
        sorted(expanded, key=lambda item: item[0].core_start)
    ):
        plans.append(
            RetryRequestPlan(
                request_id=f"retry-{index:06d}",
                candidate_ids=seed.candidate_ids,
                core=AudioInterval(
                    start_sample=seed.core_start,
                    end_sample=seed.core_end,
                ),
                context=context,
                reasons=seed.reasons,
                severity=seed.severity,
            )
        )
    return assessments, tuple(plans)


def _merge_intervals(
    intervals: Sequence[AudioInterval],
) -> tuple[AudioInterval, ...]:
    merged: list[list[int]] = []
    for interval in sorted(intervals, key=lambda item: item.start_sample):
        if merged and interval.start_sample <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], interval.end_sample)
        else:
            merged.append([interval.start_sample, interval.end_sample])
    return tuple(
        AudioInterval(start_sample=start, end_sample=end)
        for start, end in merged
    )


def _merged_covered_samples(intervals: Sequence[AudioInterval]) -> int:
    return sum(
        interval.end_sample - interval.start_sample
        for interval in _merge_intervals(intervals)
    )


def score_candidate_bundle(
    candidates: Sequence[CandidateQualityFacts],
    *,
    speech_intervals: Sequence[AudioInterval] | None,
) -> CandidateBundleScore | None:
    if not candidates:
        return None
    total_words = sum(item.word_count for item in candidates)
    valid_words = sum(item.valid_word_timestamp_count for item in candidates)
    word_coverage = valid_words / total_words if total_words else 0.0
    weights = [max(1, item.text_character_count) for item in candidates]
    total_weight = sum(weights)
    avg_logprob = sum(
        (item.avg_logprob if item.avg_logprob is not None else -2.0) * weight
        for item, weight in zip(candidates, weights, strict=True)
    ) / total_weight
    no_speech = sum(
        (item.no_speech_prob if item.no_speech_prob is not None else 1.0) * weight
        for item, weight in zip(candidates, weights, strict=True)
    ) / total_weight
    compression = max(
        item.compression_ratio if item.compression_ratio is not None else 4.0
        for item in candidates
    )
    repetition = max(item.text_repetition_score for item in candidates)
    intervals = [item.interval for item in candidates]
    timeline_intervals = _merge_intervals(intervals)
    timeline_covered = _merged_covered_samples(timeline_intervals)
    speech_covered_intervals = (
        _merge_intervals(
            tuple(
                AudioInterval(
                    start_sample=max(interval.start_sample, speech.start_sample),
                    end_sample=min(interval.end_sample, speech.end_sample),
                )
                for interval in intervals
                for speech in speech_intervals
                if min(interval.end_sample, speech.end_sample)
                > max(interval.start_sample, speech.start_sample)
            )
        )
        if speech_intervals is not None
        else None
    )
    speech_covered = (
        _merged_covered_samples(speech_covered_intervals)
        if speech_covered_intervals is not None
        else None
    )
    reason_severity = max(item.reason_severity for item in candidates)
    quality_score = (
        -2.0 * reason_severity
        + avg_logprob
        + 0.8 * word_coverage
        - 0.6 * no_speech
        - 0.25 * max(0.0, compression - 1.0)
        - repetition
    )
    return CandidateBundleScore(
        quality_score=quality_score,
        reason_severity=reason_severity,
        timeline_intervals=timeline_intervals,
        timeline_covered_samples=timeline_covered,
        speech_covered_intervals=speech_covered_intervals,
        speech_covered_samples=speech_covered,
        word_timestamp_coverage=word_coverage,
        text_character_count=sum(item.text_character_count for item in candidates),
    )


def should_select_retry(
    initial: CandidateBundleScore,
    retry: CandidateBundleScore | None,
    *,
    core_speech_samples: int | None,
    sample_rate: int,
    policy: ASRRetryPolicy = DEFAULT_RETRY_POLICY,
) -> bool:
    if sample_rate <= 0:
        raise ValueError("采样率必须大于 0")
    if retry is None:
        return core_speech_samples == 0 and initial.reason_severity > 0
    if retry.text_character_count <= 0:
        return core_speech_samples == 0 and initial.reason_severity > 0
    required_intervals = (
        initial.speech_covered_intervals
        if initial.speech_covered_intervals is not None
        else initial.timeline_intervals
    )
    tolerance_samples = round(policy.coverage_tolerance_seconds * sample_rate)
    replacement_intervals = _merge_intervals(
        tuple(
            AudioInterval(
                start_sample=max(0, interval.start_sample - tolerance_samples),
                end_sample=interval.end_sample + tolerance_samples,
            )
            for interval in retry.timeline_intervals
        )
    )
    replacement_index = 0
    for required in required_intervals:
        cursor = required.start_sample
        while (
            replacement_index < len(replacement_intervals)
            and replacement_intervals[replacement_index].end_sample <= cursor
        ):
            replacement_index += 1
        while replacement_index < len(replacement_intervals):
            replacement = replacement_intervals[replacement_index]
            if replacement.start_sample > cursor:
                break
            cursor = max(cursor, replacement.end_sample)
            if cursor >= required.end_sample:
                break
            replacement_index += 1
        if cursor < required.end_sample:
            return False
    if (
        not required_intervals
        and initial.timeline_covered_samples > 0
        and not retry.timeline_intervals
    ):
        return False
    return retry.quality_score >= initial.quality_score + policy.selection_margin
