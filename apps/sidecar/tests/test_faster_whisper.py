import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from sublingo_local.asr.diagnostics import AudioInterval
from sublingo_local.asr.faster_whisper import (
    FasterWhisperProvider,
    _analyze_vad,
    _chunk_segments_to_subtitles,
    _chunk_windows,
    _ChunkSegment,
    _deduplicate_boundary_segments,
    _dynamic_chunk_windows,
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


def test_dynamic_windows_snap_to_natural_silence_and_clip_context() -> None:
    windows = _dynamic_chunk_windows(
        130 * 16_000,
        [AudioInterval(start_sample=62 * 16_000, end_sample=64 * 16_000)],
    )

    assert [(item.core_start, item.core_end) for item in windows] == [
        (0.0, 63.0),
        (63.0, 130.0),
    ]
    assert [(item.context_start, item.context_end) for item in windows] == [
        (0.0, 65.0),
        (61.0, 130.0),
    ]
    assert [item.boundary_shift_samples for item in windows] == [3 * 16_000, 0]
    assert not any(item.fallback_to_fixed for item in windows)


def test_dynamic_windows_fall_back_to_exact_fixed_windows_without_silence() -> None:
    dynamic = _dynamic_chunk_windows(130 * 16_000, [])
    fixed = _chunk_windows(130 * 16_000)

    assert [
        (item.core_start, item.core_end, item.context_start, item.context_end)
        for item in dynamic
    ] == [
        (item.core_start, item.core_end, item.context_start, item.context_end)
        for item in fixed
    ]
    assert [item.fallback_to_fixed for item in dynamic] == [True, True, False]


def test_dynamic_windows_short_audio_uses_single_fixed_fallback() -> None:
    windows = _dynamic_chunk_windows(30 * 16_000, [])

    assert [(item.core_start, item.core_end) for item in windows] == [(0.0, 30.0)]
    assert windows[0].fallback_to_fixed is True


def test_dynamic_windows_are_deterministic_bounded_and_cover_full_timeline() -> None:
    total_samples = 240 * 16_000
    silences = [
        AudioInterval(start_sample=start * 16_000, end_sample=end * 16_000)
        for start, end in ((58, 61), (119, 122), (179, 182))
    ]

    first = _dynamic_chunk_windows(total_samples, silences)
    second = _dynamic_chunk_windows(total_samples, silences)

    assert first == second
    assert first[0].core_start == 0.0
    assert first[-1].core_end == 240.0
    assert all(
        left.core_end == right.core_start
        for left, right in zip(first, first[1:], strict=False)
    )
    assert all(45.0 <= item.core_end - item.core_start <= 75.0 for item in first)
    assert all(item.core_end > item.core_start for item in first)
    assert all(item.context_start == max(0.0, item.core_start - 2.0) for item in first)
    assert all(item.context_end == min(240.0, item.core_end + 2.0) for item in first)


def test_vad_analysis_normalizes_intervals_and_uses_stable_options() -> None:
    calls: dict[str, object] = {}

    class FakeAudio:
        def __len__(self) -> int:
            return 100

    def vad_options(**kwargs):  # type: ignore[no-untyped-def]
        calls["options"] = kwargs
        return SimpleNamespace(**kwargs)

    def get_speech_timestamps(audio, **kwargs):  # type: ignore[no-untyped-def]
        calls["audio"] = audio
        calls["vad_options"] = kwargs["vad_options"]
        calls["sampling_rate"] = kwargs["sampling_rate"]
        return [
            {"start": -10, "end": 30},
            {"start": 20, "end": 50},
            {"start": 60, "end": 60},
            {"start": 80, "end": 200},
        ]

    audio = FakeAudio()
    analysis = _analyze_vad(
        audio,
        get_speech_timestamps=get_speech_timestamps,
        vad_options_type=vad_options,
    )

    assert calls["audio"] is audio
    assert calls["sampling_rate"] == 16_000
    assert calls["options"] == {
        "threshold": 0.5,
        "min_speech_duration_ms": 0,
        "max_speech_duration_s": float("inf"),
        "min_silence_duration_ms": 350,
        "speech_pad_ms": 0,
    }
    assert [(item.start_sample, item.end_sample) for item in analysis.speech_intervals] == [
        (0, 50),
        (80, 100),
    ]
    assert [(item.start_sample, item.end_sample) for item in analysis.non_speech_intervals] == [
        (50, 80)
    ]


def test_vad_analysis_rejects_malformed_intervals() -> None:
    with pytest.raises(ValueError, match="VAD 返回了无效区间"):
        _analyze_vad(
            [0] * 100,
            get_speech_timestamps=lambda *_args, **_kwargs: [{"start": 0}],
            vad_options_type=lambda **kwargs: SimpleNamespace(**kwargs),
        )


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
            dynamic_chunking=False,
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
    assert result.diagnostics.audio.vad_source == "disabled"
    assert result.diagnostics.window_strategy == "fixed"
    assert result.diagnostics.summary.window_count == 2
    assert result.diagnostics.summary.candidate_segment_count == 2
    assert result.diagnostics.summary.output_segment_count == 2
    assert [item.candidate_count for item in result.diagnostics.windows] == [1, 1]
    assert [item.no_speech_prob for item in result.diagnostics.segments] == [0.05, 0.05]
    assert all(item.word_count == 2 for item in result.diagnostics.segments)
    assert all(item.valid_word_timestamp_count == 1 for item in result.diagnostics.segments)
    assert all(item.word_timestamp_coverage == 0.5 for item in result.diagnostics.segments)
    assert progress[-1] == 1.0


@pytest.mark.parametrize(
    "output_mode",
    [ASROutputMode.WORD_RESEGMENTED, ASROutputMode.CHUNK_SEGMENTS],
)
def test_provider_uses_vad_dynamic_windows_for_both_output_modes(
    output_mode: ASROutputMode,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {"vad_count": 0, "transcription_count": 0}

    class FakeAudio:
        def __len__(self) -> int:
            return 130 * 16_000

        def __getitem__(self, key):  # type: ignore[no-untyped-def]
            return self

    class FakeModel:
        def __init__(self, model, **kwargs):  # type: ignore[no-untyped-def]
            calls["model"] = model

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = int(calls["transcription_count"])
            calls["transcription_count"] = index + 1
            start = 1.0 if index == 0 else 3.0
            text = "前半" if index == 0 else "后半"
            segment = SimpleNamespace(
                text=text,
                start=start,
                end=start + 1.0,
                avg_logprob=-0.1,
                no_speech_prob=0.05,
                compression_ratio=1.1,
                temperature=0.0,
                words=[SimpleNamespace(word=text, start=start, end=start + 1.0)],
            )
            return iter([segment]), SimpleNamespace(
                language=kwargs["language"],
                language_probability=0.9,
            )

    class FakeVadOptions:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            calls["vad_options"] = kwargs

    def get_speech_timestamps(audio, **kwargs):  # type: ignore[no-untyped-def]
        calls["vad_count"] = int(calls["vad_count"]) + 1
        assert kwargs["sampling_rate"] == 16_000
        return [
            {"start": 0, "end": 62 * 16_000},
            {"start": 64 * 16_000, "end": 130 * 16_000},
        ]

    faster_whisper_module = ModuleType("faster_whisper")
    faster_whisper_module.WhisperModel = FakeModel  # type: ignore[attr-defined]
    audio_module = ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda path, sampling_rate: FakeAudio()  # type: ignore[attr-defined]
    vad_module = ModuleType("faster_whisper.vad")
    vad_module.VadOptions = FakeVadOptions  # type: ignore[attr-defined]
    vad_module.get_speech_timestamps = get_speech_timestamps  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad_module)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="zh",
        settings=ASRSettings(output_mode=output_mode),
    )

    assert calls["vad_count"] == 1
    assert calls["transcription_count"] == 2
    assert [item.text for item in result.segments] == ["前半", "后半"]
    assert [item.start_ms for item in result.segments] == [1_000, 64_000]
    assert result.diagnostics is not None
    assert result.diagnostics.window_strategy == "vad_dynamic"
    assert result.diagnostics.audio.vad_status == "available"
    assert [
        (item.core.start_sample, item.core.end_sample)
        for item in result.diagnostics.windows
    ] == [(0, 63 * 16_000), (63 * 16_000, 130 * 16_000)]
    assert result.diagnostics.summary.fallback_window_count == 0
    assert result.diagnostics.summary.boundary_shift_abs_total_samples == 3 * 16_000


def test_provider_falls_back_to_fixed_windows_when_boundary_vad_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAudio:
        def __len__(self) -> int:
            return 65 * 16_000

        def __getitem__(self, key):  # type: ignore[no-untyped-def]
            return self

    class FakeModel:
        transcription_calls = 0

        def __init__(self, model, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = self.transcription_calls
            self.transcription_calls += 1
            start = 1.0 if index == 0 else 3.0
            segment = SimpleNamespace(
                text=f"片段{index}",
                start=start,
                end=start + 1.0,
                avg_logprob=-0.1,
                words=[SimpleNamespace(word=f"片段{index}", start=start, end=start + 1.0)],
            )
            return iter([segment]), SimpleNamespace(
                language=kwargs["language"],
                language_probability=0.9,
            )

    faster_whisper_module = ModuleType("faster_whisper")
    faster_whisper_module.WhisperModel = FakeModel  # type: ignore[attr-defined]
    audio_module = ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda path, sampling_rate: FakeAudio()  # type: ignore[attr-defined]
    vad_module = ModuleType("faster_whisper.vad")
    vad_module.VadOptions = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]

    def fail_vad(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("sensitive local failure detail")

    vad_module.get_speech_timestamps = fail_vad  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad_module)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="zh",
        settings=ASRSettings(output_mode=ASROutputMode.CHUNK_SEGMENTS),
    )

    assert result.diagnostics is not None
    assert result.diagnostics.window_strategy == "vad_dynamic"
    assert result.diagnostics.audio.vad_status == "failed"
    assert [
        (item.core.start_sample, item.core.end_sample)
        for item in result.diagnostics.windows
    ] == [(0, 60 * 16_000), (60 * 16_000, 65 * 16_000)]
    assert [item.fallback_to_fixed for item in result.diagnostics.windows] == [
        True,
        False,
    ]
    assert result.diagnostics.summary.fallback_window_count == 1


def test_output_mode_defaults_to_word_resegmentation() -> None:
    assert ASRSettings().output_mode == ASROutputMode.WORD_RESEGMENTED
    assert ASRSettings().dynamic_chunking is True


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
