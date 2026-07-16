import math

import pytest
from pydantic import ValidationError

from sublingo_local.asr.base import TranscriptionResult
from sublingo_local.asr.diagnostics import (
    ASRAudioAnalysis,
    ASRDiagnosticsSummary,
    ASRExperimentReport,
    ASRExperimentVariant,
    ASRRunDiagnostics,
    ASRSegmentDiagnostics,
    ASRWindowDiagnostics,
    AudioInterval,
    collect_transcription_metrics,
    complement_intervals,
    normalize_intervals,
)
from sublingo_local.models import SubtitleSegment


def _single_candidate_diagnostics(**summary_updates: int) -> ASRRunDiagnostics:
    total_samples = 16_000
    summary = ASRDiagnosticsSummary(
        window_count=1,
        candidate_segment_count=1,
        output_segment_count=1,
    ).model_copy(update=summary_updates)
    return ASRRunDiagnostics(
        audio=ASRAudioAnalysis.unavailable(
            sample_rate=16_000,
            total_samples=total_samples,
        ),
        windows=(
            ASRWindowDiagnostics(
                index=0,
                core=AudioInterval(start_sample=0, end_sample=total_samples),
                context=AudioInterval(start_sample=0, end_sample=total_samples),
                candidate_count=1,
            ),
        ),
        segments=(
            ASRSegmentDiagnostics(
                candidate_id="candidate-chunk-000000-segment-000000",
                window_index=0,
                interval=AudioInterval(start_sample=0, end_sample=8_000),
            ),
        ),
        summary=summary,
    )


def test_normalize_and_complement_intervals_form_full_partition() -> None:
    speech = normalize_intervals(
        [(-20, 100), (80, 200), (500, 600), (400, 500), (900, 1_200), (700, 700)],
        total_samples=1_000,
    )

    assert [(item.start_sample, item.end_sample) for item in speech] == [
        (0, 200),
        (400, 600),
        (900, 1_000),
    ]
    non_speech = complement_intervals(speech, total_samples=1_000)
    assert [(item.start_sample, item.end_sample) for item in non_speech] == [
        (200, 400),
        (600, 900),
    ]


def test_audio_analysis_requires_normalized_intervals_and_keeps_one_truth() -> None:
    with pytest.raises(ValidationError, match="排序、裁剪且合并"):
        ASRAudioAnalysis(
            sample_rate=16_000,
            total_samples=1_000,
            vad_source="faster_whisper",
            vad_status="available",
            speech_intervals=(
                AudioInterval(start_sample=400, end_sample=600),
                AudioInterval(start_sample=100, end_sample=200),
            ),
        )

    analysis = ASRAudioAnalysis(
        sample_rate=16_000,
        total_samples=1_000,
        vad_source="faster_whisper",
        vad_status="available",
        speech_intervals=(AudioInterval(start_sample=100, end_sample=800),),
    )

    assert analysis.non_speech_intervals == (
        AudioInterval(start_sample=0, end_sample=100),
        AudioInterval(start_sample=800, end_sample=1_000),
    )
    assert "non_speech" not in analysis.model_dump(mode="json")


def test_run_diagnostics_rejects_drifting_summary() -> None:
    audio = ASRAudioAnalysis.unavailable(sample_rate=16_000, total_samples=32_000)
    window = ASRWindowDiagnostics(
        index=0,
        core=AudioInterval(start_sample=0, end_sample=32_000),
        context=AudioInterval(start_sample=0, end_sample=32_000),
        candidate_count=1,
    )
    segment = ASRSegmentDiagnostics(
        candidate_id="candidate-chunk-000000-segment-000000",
        window_index=0,
        interval=AudioInterval(start_sample=1_000, end_sample=2_000),
        avg_logprob=-0.25,
        no_speech_prob=0.1,
        compression_ratio=1.2,
        temperature=0.0,
        word_count=2,
        valid_word_timestamp_count=1,
        word_timestamp_coverage=0.5,
    )

    with pytest.raises(ValidationError, match="候选片段汇总数量"):
        ASRRunDiagnostics(
            audio=audio,
            windows=(window,),
            segments=(segment,),
            summary=ASRDiagnosticsSummary(window_count=1),
        )


def test_run_diagnostics_requires_full_core_coverage() -> None:
    with pytest.raises(ValidationError, match="覆盖完整音频"):
        ASRRunDiagnostics(
            audio=ASRAudioAnalysis.unavailable(
                sample_rate=16_000,
                total_samples=1_000,
            ),
            windows=(
                ASRWindowDiagnostics(
                    index=0,
                    core=AudioInterval(start_sample=0, end_sample=900),
                    context=AudioInterval(start_sample=0, end_sample=900),
                ),
            ),
            summary=ASRDiagnosticsSummary(window_count=1),
        )


def test_candidate_ids_cannot_masquerade_as_final_subtitle_ids() -> None:
    with pytest.raises(ValidationError, match="candidate- 专用命名空间"):
        ASRSegmentDiagnostics(
            candidate_id="seg-000001",
            window_index=0,
            interval=AudioInterval(start_sample=0, end_sample=1),
        )


@pytest.mark.parametrize(
    ("summary_updates", "message"),
    [
        ({"retry_candidate_count": 2}, "重试候选数量"),
        (
            {"retry_candidate_count": 1, "retry_request_count": 2},
            "二次识别请求数量",
        ),
        (
            {
                "retry_candidate_count": 1,
                "retry_request_count": 0,
                "retry_selected_count": 1,
            },
            "采用二次结果数量",
        ),
    ],
)
def test_retry_summary_counts_are_bounded(
    summary_updates: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _single_candidate_diagnostics(**summary_updates)


def test_transcription_result_binds_output_summary_to_subtitle_count() -> None:
    with pytest.raises(ValidationError, match="输出片段汇总数量"):
        TranscriptionResult(
            language="ja",
            segments=[],
            diagnostics=_single_candidate_diagnostics(),
        )


def test_old_transcription_artifact_loads_without_diagnostics() -> None:
    result = TranscriptionResult.model_validate(
        {
            "language": "ja",
            "duration_seconds": 1.0,
            "segments": [
                {"id": "seg-000001", "start_ms": 0, "end_ms": 1_000, "text": "字幕"}
            ],
        }
    )

    assert result.diagnostics is None


def test_experiment_report_contains_only_opaque_ids_and_numeric_metrics() -> None:
    segments = [
        SubtitleSegment(id="seg-000001", start_ms=0, end_ms=800, text="秘密 字幕"),
        SubtitleSegment(id="seg-000002", start_ms=700, end_ms=1_200, text="第二句"),
    ]
    metrics = collect_transcription_metrics(segments, diagnostics=None)
    baseline = ASRExperimentVariant(
        name="fixed",
        config_fingerprint="a" * 64,
        elapsed_ms=123,
        metrics=metrics,
    )
    report = ASRExperimentReport(
        fixture_id="sample-ja-01",
        media_fingerprint="b" * 64,
        baseline=baseline,
        candidate=ASRExperimentVariant(
            name="candidate",
            config_fingerprint="c" * 64,
            elapsed_ms=111,
            metrics=metrics,
        ),
    )

    payload = report.model_dump_json()
    assert metrics == {
        "subtitle_count": 2,
        "text_character_count": 7,
        "timeline_covered_ms": 1_200,
    }
    assert "秘密" not in payload
    assert "字幕" not in payload

    with pytest.raises(ValidationError, match="只允许数值指标"):
        ASRExperimentVariant(
            name="unsafe",
            config_fingerprint="d" * 64,
            elapsed_ms=1,
            metrics={"raw_text": "do not persist"},
        )
    with pytest.raises(ValidationError, match="有限数值"):
        ASRExperimentVariant(
            name="invalid",
            config_fingerprint="e" * 64,
            elapsed_ms=1,
            metrics={"score": math.inf},
        )
