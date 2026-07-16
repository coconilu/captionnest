import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from sublingo_local.asr.faster_whisper import (
    FasterWhisperProvider,
    _chunk_segments_to_subtitles,
    _chunk_windows,
    _ChunkSegment,
    _deduplicate_boundary_segments,
    _optional_finite_float,
    _optional_non_negative_float,
    _optional_probability,
    _valid_word_offsets,
    _word_resegmented_subtitles,
    _WordItem,
)
from sublingo_local.models import ASROutputMode, ASRSettings


def test_chunk_windows_use_non_overlapping_cores_with_context() -> None:
    windows = _chunk_windows(130 * 16_000)

    assert [(item.core_start, item.core_end) for item in windows] == [
        (0.0, 60.0),
        (60.0, 120.0),
        (120.0, 130.0),
    ]
    assert [(item.context_start, item.context_end) for item in windows] == [
        (0.0, 62.0),
        (58.0, 122.0),
        (118.0, 130.0),
    ]


def test_word_resegmentation_preserves_text_and_splits_long_silence() -> None:
    parent = _ChunkSegment(
        text="今日は晴れ。",
        start=0.0,
        end=5.6,
        words=(
            _WordItem(text="今日は", start=0.0, end=0.5),
            _WordItem(text="晴れ", start=5.0, end=5.5),
            _WordItem(text="。", start=5.5, end=5.6),
        ),
        chunk_index=0,
        avg_logprob=-0.1,
    )

    segments = _word_resegmented_subtitles(
        [parent],
        language="ja",
        duration_seconds=10.0,
    )

    assert [item.text for item in segments] == ["今日は", "晴れ。"]
    assert "".join(item.text for item in segments) == parent.text
    assert [(item.start_ms, item.end_ms) for item in segments] == [
        (0, 500),
        (5_000, 5_600),
    ]


def test_chunk_segment_mode_keeps_model_boundaries() -> None:
    parent = _ChunkSegment(
        text="モデルが返した一段落",
        start=10.0,
        end=38.0,
        words=(),
        chunk_index=0,
        avg_logprob=-0.2,
    )

    segments = _chunk_segments_to_subtitles([parent])

    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [
        (10_000, 38_000, "モデルが返した一段落")
    ]


def test_boundary_deduplication_keeps_higher_confidence_copy() -> None:
    lower = _ChunkSegment(
        text="こんにちは。",
        start=59.0,
        end=60.4,
        words=(),
        chunk_index=0,
        avg_logprob=-0.8,
    )
    higher = _ChunkSegment(
        text="こんにちは",
        start=59.1,
        end=60.5,
        words=(),
        chunk_index=1,
        avg_logprob=-0.2,
    )

    assert _deduplicate_boundary_segments([lower, higher]) == [higher]


def test_provider_votes_across_video_then_transcribes_each_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {"languages": []}

    class FakeAudio:
        def __len__(self) -> int:
            return 65 * 16_000

        def __getitem__(self, key):  # type: ignore[no-untyped-def]
            return self

    class FakeModel:
        detection_calls = 0
        transcription_calls = 0

        def __init__(self, model, **kwargs):  # type: ignore[no-untyped-def]
            calls["model"] = model
            calls["load_kwargs"] = kwargs

        def detect_language(self, **kwargs):  # type: ignore[no-untyped-def]
            languages = ["zh", "ja", "ja", "ja", "ja"]
            language = languages[self.detection_calls]
            self.detection_calls += 1
            return language, 0.9, []

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = self.transcription_calls
            self.transcription_calls += 1
            calls["languages"].append(kwargs["language"])  # type: ignore[union-attr]
            start = 1.0 if index == 0 else 3.0
            text = "前半" if index == 0 else "後半"
            segment = SimpleNamespace(
                text=text,
                start=start,
                end=start + 1.0,
                avg_logprob=-0.1,
                no_speech_prob=0.05,
                compression_ratio=1.1,
                temperature=0.0,
                words=[
                    SimpleNamespace(word=text, start=start, end=start + 1.0),
                    SimpleNamespace(word="invalid", start=2.0, end=1.0),
                ],
            )
            return iter([segment]), SimpleNamespace(
                language=kwargs["language"],
                language_probability=0.9,
            )

    faster_whisper_module = ModuleType("faster_whisper")
    faster_whisper_module.WhisperModel = FakeModel  # type: ignore[attr-defined]
    audio_module = ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda path, sampling_rate: FakeAudio()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    progress: list[float] = []

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="auto",
        settings=ASRSettings(
            model="large-v3",
            device="cuda",
            compute_type="float16",
            output_mode="chunk_segments",
        ),
        on_progress=progress.append,
    )

    assert calls["model"] == "large-v3"
    assert calls["load_kwargs"] == {"device": "cuda", "compute_type": "float16"}
    assert calls["languages"] == ["ja", "ja"]
    assert result.language == "ja"
    assert result.duration_seconds == 65.0
    assert [item.text for item in result.segments] == ["前半", "後半"]
    assert [item.start_ms for item in result.segments] == [1_000, 61_000]
    assert result.diagnostics is not None
    assert result.diagnostics.audio.vad_status == "unavailable"
    assert result.diagnostics.summary.window_count == 2
    assert result.diagnostics.summary.candidate_segment_count == 2
    assert result.diagnostics.summary.output_segment_count == 2
    assert [item.candidate_count for item in result.diagnostics.windows] == [1, 1]
    assert [item.no_speech_prob for item in result.diagnostics.segments] == [0.05, 0.05]
    assert all(item.word_count == 2 for item in result.diagnostics.segments)
    assert all(item.valid_word_timestamp_count == 1 for item in result.diagnostics.segments)
    assert all(item.word_timestamp_coverage == 0.5 for item in result.diagnostics.segments)
    assert progress[-1] == 1.0


def test_output_mode_defaults_to_word_resegmentation() -> None:
    assert ASRSettings().output_mode == ASROutputMode.WORD_RESEGMENTED


def test_non_finite_diagnostics_are_normalized_to_unknown() -> None:
    assert _optional_finite_float(float("nan")) is None
    assert _optional_finite_float(float("inf")) is None
    assert _optional_finite_float("not-a-number") is None
    assert _optional_finite_float(-0.5) == -0.5
    assert _optional_probability(1.1) is None
    assert _optional_probability(0.25) == 0.25
    assert _optional_non_negative_float(-0.1) is None
    assert _optional_non_negative_float(1.2) == 1.2


def test_word_timestamp_validity_rejects_reverse_and_out_of_window_ranges() -> None:
    assert _valid_word_offsets(0.0, 1.0, window_duration=1.0)
    assert not _valid_word_offsets(1.0, 0.5, window_duration=2.0)
    assert not _valid_word_offsets(-0.1, 0.5, window_duration=2.0)
    assert not _valid_word_offsets(0.0, 1.0005, window_duration=1.0)
    assert not _valid_word_offsets(0.5, 2.1, window_duration=2.0)
