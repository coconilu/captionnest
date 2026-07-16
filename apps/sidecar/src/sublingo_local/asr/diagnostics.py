from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..models import SubtitleSegment

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_CANDIDATE_ID_PATTERN = re.compile(r"^candidate-[a-z0-9][a-z0-9._-]{0,85}$")
_METRIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AudioInterval(BaseModel):
    """A half-open interval on the decoded audio sample timeline."""

    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)

    @model_validator(mode="after")
    def end_after_start(self) -> AudioInterval:
        if self.end_sample <= self.start_sample:
            raise ValueError("区间结束位置必须晚于开始位置")
        return self


def normalize_intervals(
    intervals: Iterable[AudioInterval | tuple[int, int]],
    *,
    total_samples: int,
) -> tuple[AudioInterval, ...]:
    """Clamp, sort and merge intervals into a deterministic non-overlapping tuple."""

    if total_samples <= 0:
        raise ValueError("音频总采样数必须大于 0")

    clipped: list[tuple[int, int]] = []
    for value in intervals:
        if isinstance(value, AudioInterval):
            start_sample, end_sample = value.start_sample, value.end_sample
        else:
            start_sample, end_sample = value
        start_sample = max(0, min(total_samples, int(start_sample)))
        end_sample = max(0, min(total_samples, int(end_sample)))
        if end_sample > start_sample:
            clipped.append((start_sample, end_sample))

    merged: list[list[int]] = []
    for start_sample, end_sample in sorted(clipped):
        if merged and start_sample <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end_sample)
        else:
            merged.append([start_sample, end_sample])
    return tuple(
        AudioInterval(start_sample=start_sample, end_sample=end_sample)
        for start_sample, end_sample in merged
    )


def complement_intervals(
    speech_intervals: Sequence[AudioInterval],
    *,
    total_samples: int,
) -> tuple[AudioInterval, ...]:
    """Derive non-speech from normalized speech intervals without a second truth source."""

    normalized = normalize_intervals(speech_intervals, total_samples=total_samples)
    non_speech: list[AudioInterval] = []
    cursor = 0
    for interval in normalized:
        if interval.start_sample > cursor:
            non_speech.append(
                AudioInterval(start_sample=cursor, end_sample=interval.start_sample)
            )
        cursor = interval.end_sample
    if cursor < total_samples:
        non_speech.append(AudioInterval(start_sample=cursor, end_sample=total_samples))
    return tuple(non_speech)


class ASRAudioAnalysis(BaseModel):
    """Reusable VAD analysis shared by dynamic windows, retries and timestamp cleanup."""

    schema_version: Literal[1] = 1
    sample_rate: int = Field(gt=0)
    total_samples: int = Field(gt=0)
    vad_source: str = Field(min_length=1, max_length=64)
    vad_status: Literal["available", "unavailable", "failed"]
    speech_intervals: tuple[AudioInterval, ...] = ()

    @field_validator("vad_source")
    @classmethod
    def stable_vad_source(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("VAD source 必须是稳定标识符")
        return value

    @model_validator(mode="after")
    def validate_speech_intervals(self) -> ASRAudioAnalysis:
        normalized = normalize_intervals(
            self.speech_intervals,
            total_samples=self.total_samples,
        )
        if normalized != self.speech_intervals:
            raise ValueError("语音区间必须已排序、裁剪且合并")
        if self.vad_status != "available" and self.speech_intervals:
            raise ValueError("不可用的 VAD 分析不能携带语音区间")
        return self

    @classmethod
    def unavailable(
        cls,
        *,
        sample_rate: int,
        total_samples: int,
        vad_source: str = "unavailable",
    ) -> ASRAudioAnalysis:
        return cls(
            sample_rate=sample_rate,
            total_samples=total_samples,
            vad_source=vad_source,
            vad_status="unavailable",
        )

    @classmethod
    def failed(
        cls,
        *,
        sample_rate: int,
        total_samples: int,
        vad_source: str,
    ) -> ASRAudioAnalysis:
        return cls(
            sample_rate=sample_rate,
            total_samples=total_samples,
            vad_source=vad_source,
            vad_status="failed",
        )

    @property
    def non_speech_intervals(self) -> tuple[AudioInterval, ...]:
        if self.vad_status != "available":
            return ()
        return complement_intervals(
            self.speech_intervals,
            total_samples=self.total_samples,
        )


class ASRWindowDiagnostics(BaseModel):
    index: int = Field(ge=0)
    core: AudioInterval
    context: AudioInterval
    boundary_shift_samples: int = 0
    fallback_to_fixed: bool = False
    candidate_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def context_contains_core(self) -> ASRWindowDiagnostics:
        if (
            self.context.start_sample > self.core.start_sample
            or self.context.end_sample < self.core.end_sample
        ):
            raise ValueError("窗口上下文必须完整包含核心区间")
        return self


class ASRSegmentDiagnostics(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=96)
    window_index: int = Field(ge=0)
    interval: AudioInterval
    avg_logprob: float | None = None
    no_speech_prob: float | None = Field(default=None, ge=0, le=1)
    compression_ratio: float | None = Field(default=None, ge=0)
    temperature: float | None = Field(default=None, ge=0)
    word_count: int = Field(default=0, ge=0)
    valid_word_timestamp_count: int = Field(default=0, ge=0)
    word_timestamp_coverage: float = Field(default=0, ge=0, le=1)

    @field_validator("candidate_id")
    @classmethod
    def stable_candidate_id(cls, value: str) -> str:
        if not _CANDIDATE_ID_PATTERN.fullmatch(value):
            raise ValueError("候选片段 ID 必须使用 candidate- 专用命名空间")
        return value

    @field_validator("avg_logprob", "no_speech_prob", "compression_ratio", "temperature")
    @classmethod
    def finite_optional_metric(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("诊断指标必须是有限数值")
        return value

    @model_validator(mode="after")
    def validate_word_coverage(self) -> ASRSegmentDiagnostics:
        if self.valid_word_timestamp_count > self.word_count:
            raise ValueError("有效词时间戳数量不能超过词数量")
        expected = self.valid_word_timestamp_count / self.word_count if self.word_count else 0.0
        if not math.isclose(self.word_timestamp_coverage, expected, abs_tol=1e-9):
            raise ValueError("词时间戳覆盖率与计数不一致")
        return self


class ASRDiagnosticsSummary(BaseModel):
    window_count: int = Field(default=0, ge=0)
    fallback_window_count: int = Field(default=0, ge=0)
    boundary_shift_abs_total_samples: int = Field(default=0, ge=0)
    candidate_segment_count: int = Field(default=0, ge=0)
    deduplicated_segment_count: int = Field(default=0, ge=0)
    output_segment_count: int = Field(default=0, ge=0)
    retry_candidate_count: int = Field(default=0, ge=0)
    retry_request_count: int = Field(default=0, ge=0)
    retry_selected_count: int = Field(default=0, ge=0)


class ASRRunDiagnostics(BaseModel):
    schema_version: Literal[1] = 1
    window_strategy: Literal["fixed", "vad_dynamic"] = "fixed"
    audio: ASRAudioAnalysis
    windows: tuple[ASRWindowDiagnostics, ...] = ()
    segments: tuple[ASRSegmentDiagnostics, ...] = ()
    summary: ASRDiagnosticsSummary = Field(default_factory=ASRDiagnosticsSummary)

    @model_validator(mode="after")
    def summary_matches_details(self) -> ASRRunDiagnostics:
        if not self.windows:
            raise ValueError("ASR 诊断必须包含至少一个窗口")
        expected_indexes = list(range(len(self.windows)))
        if [window.index for window in self.windows] != expected_indexes:
            raise ValueError("窗口索引必须从 0 连续递增")

        cursor = 0
        candidate_counts = [0 for _ in self.windows]
        for window in self.windows:
            if window.core.start_sample != cursor:
                raise ValueError("窗口核心区必须首尾相接且无空洞")
            if window.context.end_sample > self.audio.total_samples:
                raise ValueError("窗口上下文不能超出音频范围")
            cursor = window.core.end_sample
        if cursor != self.audio.total_samples:
            raise ValueError("窗口核心区必须覆盖完整音频")

        candidate_ids: set[str] = set()
        for segment in self.segments:
            if segment.candidate_id in candidate_ids:
                raise ValueError("候选片段 ID 不能重复")
            candidate_ids.add(segment.candidate_id)
            if segment.window_index >= len(self.windows):
                raise ValueError("候选片段引用了不存在的窗口")
            if segment.interval.end_sample > self.audio.total_samples:
                raise ValueError("候选片段不能超出音频范围")
            candidate_counts[segment.window_index] += 1
        if candidate_counts != [window.candidate_count for window in self.windows]:
            raise ValueError("窗口候选数量与片段明细不一致")

        if self.summary.window_count != len(self.windows):
            raise ValueError("窗口汇总数量与明细不一致")
        if self.summary.candidate_segment_count != len(self.segments):
            raise ValueError("候选片段汇总数量与明细不一致")
        fallback_count = sum(window.fallback_to_fixed for window in self.windows)
        if self.summary.fallback_window_count != fallback_count:
            raise ValueError("固定切片回退数量与窗口明细不一致")
        shift_total = sum(abs(window.boundary_shift_samples) for window in self.windows)
        if self.summary.boundary_shift_abs_total_samples != shift_total:
            raise ValueError("边界吸附距离汇总与窗口明细不一致")
        if self.summary.deduplicated_segment_count > self.summary.candidate_segment_count:
            raise ValueError("去重数量不能超过候选片段数量")
        if self.summary.retry_candidate_count > self.summary.candidate_segment_count:
            raise ValueError("重试候选数量不能超过候选片段数量")
        if self.summary.retry_request_count > self.summary.retry_candidate_count:
            raise ValueError("二次识别请求数量不能超过重试候选数量")
        if self.summary.retry_selected_count > self.summary.retry_request_count:
            raise ValueError("采用二次结果数量不能超过二次识别请求数量")
        return self


class ASRExperimentVariant(BaseModel):
    """Text-free metrics for one side of a reproducible ASR A/B run."""

    name: str = Field(min_length=1, max_length=64)
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    elapsed_ms: int = Field(ge=0)
    metrics: dict[str, int | float | None]

    @field_validator("name")
    @classmethod
    def stable_name(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("实验变体名称必须是稳定标识符")
        return value

    @field_validator("metrics", mode="before")
    @classmethod
    def numeric_metrics_only(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("实验指标必须是键值对象")
        for key, metric in value.items():
            if not isinstance(key, str) or not _METRIC_KEY_PATTERN.fullmatch(key):
                raise ValueError("实验指标名称无效")
            if isinstance(metric, bool) or (
                metric is not None and not isinstance(metric, (int, float))
            ):
                raise ValueError("实验报告只允许数值指标")
            if isinstance(metric, float) and not math.isfinite(metric):
                raise ValueError("实验指标必须是有限数值")
        return value


class ASRExperimentReport(BaseModel):
    schema_version: Literal[1] = 1
    fixture_id: str = Field(min_length=1, max_length=96)
    media_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline: ASRExperimentVariant
    candidate: ASRExperimentVariant

    @field_validator("fixture_id")
    @classmethod
    def stable_fixture_id(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("实验夹具 ID 必须是稳定标识符")
        return value

    @model_validator(mode="after")
    def distinct_variants(self) -> ASRExperimentReport:
        if self.baseline.name == self.candidate.name:
            raise ValueError("A/B 实验必须使用不同的变体名称")
        return self


def _covered_duration_ms(intervals: Iterable[tuple[int, int]]) -> int:
    merged: list[list[int]] = []
    for start_ms, end_ms in sorted(intervals):
        if end_ms <= start_ms:
            continue
        if merged and start_ms <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end_ms)
        else:
            merged.append([start_ms, end_ms])
    return sum(end_ms - start_ms for start_ms, end_ms in merged)


def collect_transcription_metrics(
    segments: Sequence[SubtitleSegment],
    diagnostics: ASRRunDiagnostics | None,
) -> dict[str, int | float | None]:
    """Return aggregate A/B metrics without retaining subtitle text or media paths."""

    covered_ms = _covered_duration_ms((item.start_ms, item.end_ms) for item in segments)
    metrics: dict[str, int | float | None] = {
        "subtitle_count": len(segments),
        "text_character_count": sum(len(re.sub(r"\s+", "", item.text)) for item in segments),
        "timeline_covered_ms": covered_ms,
    }
    if diagnostics is not None:
        summary = diagnostics.summary
        metrics.update(
            timeline_covered_samples=round(
                covered_ms * diagnostics.audio.sample_rate / 1_000
            ),
            window_count=summary.window_count,
            fallback_window_count=summary.fallback_window_count,
            boundary_shift_abs_total_samples=summary.boundary_shift_abs_total_samples,
            candidate_segment_count=summary.candidate_segment_count,
            deduplicated_segment_count=summary.deduplicated_segment_count,
            retry_candidate_count=summary.retry_candidate_count,
            retry_request_count=summary.retry_request_count,
            retry_selected_count=summary.retry_selected_count,
        )
    return metrics
