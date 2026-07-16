import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from sublingo_local.asr.diagnostics import ASRAudioAnalysis, AudioInterval
from sublingo_local.asr.faster_whisper import (
    FasterWhisperProvider,
    _analyze_vad,
    _chunk_segments_to_subtitles,
    _chunk_windows,
    _ChunkSegment,
    _deduplicate_boundary_segments,
    _dynamic_chunk_windows,
    _normalize_chunk_segment_timestamps,
    _optional_finite_float,
    _optional_non_negative_float,
    _optional_probability,
    _preserve_equivalent_initial_timeline,
    _serialize_hotwords,
    _valid_word_offsets,
    _validate_hotword_token_budget,
    _word_resegmented_subtitles,
    _WordItem,
)
from sublingo_local.models import ASROutputMode, ASRSettings


def test_serialize_hotwords_adapts_validated_list_for_faster_whisper() -> None:
    assert _serialize_hotwords([]) is None
    assert _serialize_hotwords(["CaptionNest", "初音未来", "葬送のフリーレン"]) == (
        "CaptionNest, 初音未来, 葬送のフリーレン"
    )


def test_validate_hotword_token_budget_matches_faster_whisper_boundary() -> None:
    class FakeTokenizer:
        def __init__(self, token_count: int) -> None:
            self.token_count = token_count

        def encode(self, text: str, *, add_special_tokens: bool):
            assert text.startswith(" ")
            assert add_special_tokens is False
            return SimpleNamespace(ids=range(self.token_count))

    model = SimpleNamespace(
        hf_tokenizer=FakeTokenizer(223),
        max_length=448,
    )
    assert _validate_hotword_token_budget(model, "CaptionNest") == 223

    model.hf_tokenizer = FakeTokenizer(224)
    with pytest.raises(ValueError, match="224 个 Token.*223 个 Token") as exc_info:
        _validate_hotword_token_budget(model, "DO-NOT-LEAK-HOTWORD")
    assert "DO-NOT-LEAK-HOTWORD" not in str(exc_info.value)


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


def test_word_resegmentation_freezes_grouping_before_timestamp_normalization() -> None:
    original = _ChunkSegment(
        text="hello world",
        start=0.0,
        end=2.19,
        words=(
            _WordItem(text="hello ", start=0.0, end=0.5),
            _WordItem(text="world", start=1.69, end=2.19),
        ),
        chunk_index=0,
        avg_logprob=-0.1,
    )
    normalized = _ChunkSegment(
        text=original.text,
        start=0.0,
        end=2.21,
        words=(
            original.words[0],
            _WordItem(text="world", start=1.71, end=2.21),
        ),
        chunk_index=original.chunk_index,
        avg_logprob=original.avg_logprob,
    )

    baseline = _word_resegmented_subtitles(
        [original],
        language="en",
        duration_seconds=3.0,
    )
    adjusted = _word_resegmented_subtitles(
        [normalized],
        language="en",
        duration_seconds=3.0,
        grouping_items=[original],
    )

    assert [(item.id, item.text) for item in adjusted] == [
        (item.id, item.text) for item in baseline
    ]
    assert [(item.start_ms, item.end_ms) for item in adjusted] == [(0, 2_210)]


def test_normalized_equal_intervals_preserve_text_and_ids_in_both_modes() -> None:
    original = [
        _ChunkSegment(
            text="z-first",
            start=0.1,
            end=1.0,
            words=(),
            chunk_index=0,
            avg_logprob=-0.1,
        ),
        _ChunkSegment(
            text="a-second",
            start=0.15,
            end=1.05,
            words=(),
            chunk_index=0,
            avg_logprob=-0.1,
        ),
    ]
    analysis = ASRAudioAnalysis(
        sample_rate=16_000,
        total_samples=32_000,
        vad_source="test_vad",
        vad_status="available",
        speech_intervals=(
            AudioInterval(start_sample=3_200, end_sample=14_400),
        ),
    )

    normalized, _, _ = _normalize_chunk_segment_timestamps(
        original,
        audio_analysis=analysis,
    )
    word_baseline = _word_resegmented_subtitles(
        original,
        language="en",
        duration_seconds=2.0,
    )
    word_candidate = _word_resegmented_subtitles(
        normalized,
        language="en",
        duration_seconds=2.0,
        grouping_items=original,
    )
    chunk_baseline = _chunk_segments_to_subtitles(original)
    chunk_candidate = _chunk_segments_to_subtitles(normalized)

    assert [(item.start, item.end) for item in normalized] == [
        (0.2, 0.9),
        (0.2, 0.9),
    ]
    assert [(item.id, item.text) for item in word_candidate] == [
        (item.id, item.text) for item in word_baseline
    ]
    assert [(item.id, item.text) for item in chunk_candidate] == [
        (item.id, item.text) for item in chunk_baseline
    ]


def test_readability_silence_cap_respects_timestamp_shift_limit() -> None:
    parent = _ChunkSegment(
        text="a",
        start=0.0,
        end=0.05,
        words=(_WordItem(text="a", start=0.0, end=0.05),),
        chunk_index=0,
        avg_logprob=-0.1,
    )

    within_limit = _word_resegmented_subtitles(
        [parent],
        language="en",
        duration_seconds=1.0,
        readability_silences=[AudioInterval(start_sample=100, end_sample=200)],
        readability_sample_rate=1_000,
    )
    beyond_limit = _word_resegmented_subtitles(
        [parent],
        language="en",
        duration_seconds=1.0,
        readability_silences=[AudioInterval(start_sample=80, end_sample=200)],
        readability_sample_rate=1_000,
    )

    assert within_limit[0].end_ms == 100
    assert beyond_limit[0].end_ms == 400


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


def test_equivalent_retry_keeps_initial_program_owned_timeline() -> None:
    initial_word = _WordItem(text="same", start=5.0, end=6.0)
    initial = _ChunkSegment(
        text="same",
        start=5.0,
        end=6.0,
        words=(initial_word,),
        chunk_index=0,
        avg_logprob=-1.6,
        candidate_id="candidate-initial",
    )
    retry = _ChunkSegment(
        text="same",
        start=4.98,
        end=5.97,
        words=(_WordItem(text="same", start=4.98, end=5.97),),
        chunk_index=1,
        avg_logprob=-0.1,
        candidate_id="candidate-retry",
    )

    preserved = _preserve_equivalent_initial_timeline([initial], [retry])

    assert preserved == [
        _ChunkSegment(
            text="same",
            start=5.0,
            end=6.0,
            words=(initial_word,),
            chunk_index=0,
            avg_logprob=-0.1,
            candidate_id="candidate-retry",
        )
    ]


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
            selective_retry=False,
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


def _install_selective_retry_fakes(
    monkeypatch: pytest.MonkeyPatch,
    calls: dict[str, object],
    *,
    fail_retry: bool = False,
    empty_retry: bool = False,
    near_silence: bool = False,
    confirmed_silence: bool = False,
    timestamp_intrusion: bool = False,
    timestamp_interval: tuple[float, float] | None = None,
    audio_samples: int = 30 * 16_000,
) -> None:
    class FakeAudio:
        def __len__(self) -> int:
            return audio_samples

        def __getitem__(self, key):  # type: ignore[no-untyped-def]
            calls.setdefault("slices", []).append((key.start, key.stop))  # type: ignore[union-attr]
            return self

    class FakeModel:
        transcription_calls = 0

        def __init__(self, model, **kwargs):  # type: ignore[no-untyped-def]
            calls["model"] = model
            self.hf_tokenizer = SimpleNamespace(
                encode=lambda text, add_special_tokens=False: SimpleNamespace(
                    ids=range(len(text))
                )
            )
            self.max_length = 448

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = self.transcription_calls
            self.transcription_calls += 1
            calls.setdefault("transcribe_kwargs", []).append(kwargs)  # type: ignore[union-attr]
            if index == 1 and fail_retry:
                raise RuntimeError("raw provider detail must not be persisted")
            if index == 1 and empty_retry:
                return iter([]), SimpleNamespace(
                    language=kwargs["language"],
                    language_probability=0.9,
                )
            is_retry = index == 1
            text = "清晰片段" if is_retry else "疑似片段"
            if timestamp_interval is not None:
                start, end = timestamp_interval
            else:
                start = 4.0 if is_retry else (3.9 if timestamp_intrusion else 5.0)
                end = 8.1 if timestamp_intrusion and not is_retry else start + 2.0
            segment = SimpleNamespace(
                text=text,
                start=start,
                end=end,
                avg_logprob=(
                    -0.1
                    if is_retry
                    else (-0.3 if near_silence or confirmed_silence else -1.6)
                ),
                no_speech_prob=(
                    0.75
                    if (near_silence or confirmed_silence) and not is_retry
                    else 0.1
                ),
                compression_ratio=1.2,
                temperature=0.0,
                words=[
                    SimpleNamespace(word=text, start=start, end=end),
                ],
            )
            return iter([segment]), SimpleNamespace(
                language=kwargs["language"],
                language_probability=0.9,
            )

    class FakeVadOptions:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            calls["vad_options"] = kwargs

    def get_speech_timestamps(audio, **kwargs):  # type: ignore[no-untyped-def]
        calls["vad_count"] = int(calls.get("vad_count", 0)) + 1
        if timestamp_interval is not None:
            return [{"start": 0, "end": min(audio_samples, 16_000)}]
        if confirmed_silence:
            return []
        if near_silence:
            return [{"start": 5 * 16_000, "end": round(5.1 * 16_000)}]
        return [{"start": 4 * 16_000, "end": 8 * 16_000}]

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


@pytest.mark.parametrize(
    "output_mode",
    [ASROutputMode.WORD_RESEGMENTED, ASROutputMode.CHUNK_SEGMENTS],
)
def test_provider_selectively_retries_only_suspicious_core_once(
    output_mode: ASROutputMode,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(monkeypatch, calls)
    progress: list[float] = []

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="ja",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=output_mode,
        ),
        on_progress=progress.append,
    )

    transcribe_kwargs = calls["transcribe_kwargs"]
    assert isinstance(transcribe_kwargs, list)
    assert len(transcribe_kwargs) == 2
    assert transcribe_kwargs[1] == {
        "language": "ja",
        "task": "transcribe",
        "beam_size": 8,
        "patience": 1.2,
        "temperature": 0.0,
        "repetition_penalty": 1.1,
        "no_repeat_ngram_size": 3,
        "vad_filter": True,
        "word_timestamps": True,
        "condition_on_previous_text": False,
    }
    assert all("hotwords" not in kwargs for kwargs in transcribe_kwargs)
    assert calls["vad_count"] == 1
    assert calls["slices"] == [(0, 30 * 16_000), (1 * 16_000, 11 * 16_000)]
    assert [segment.text for segment in result.segments] == ["清晰片段"]
    assert progress == sorted(progress)
    assert progress[-1] == 1.0

    diagnostics = result.diagnostics
    assert diagnostics is not None
    assert diagnostics.window_strategy == "fixed"
    assert diagnostics.audio.vad_status == "available"
    assert diagnostics.summary.candidate_segment_count == 2
    assert diagnostics.summary.retry_candidate_count == 1
    assert diagnostics.summary.retry_request_count == 1
    assert diagnostics.summary.retry_selected_count == 1
    assert diagnostics.summary.retry_initial_selected_count == 0
    assert diagnostics.summary.retry_failed_count == 0
    assert diagnostics.summary.retry_reason_counts == {"low_avg_logprob": 1}
    assert diagnostics.retries[0].status == "selected_retry"
    assert diagnostics.retries[0].retry_segment_count == 1
    assert diagnostics.retries[0].context == AudioInterval(
        start_sample=1 * 16_000,
        end_sample=11 * 16_000,
    )
    assert [window.candidate_count for window in diagnostics.windows] == [2]
    diagnostics_payload = diagnostics.model_dump_json()
    assert "疑似片段" not in diagnostics_payload
    assert "清晰片段" not in diagnostics_payload


def test_provider_passes_same_normalized_hotwords_to_first_pass_and_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(monkeypatch, calls)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="ja",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=ASROutputMode.CHUNK_SEGMENTS,
            hotwords=[" CaptionNest ", "初音未来", "CaptionNest"],
        ),
    )

    transcribe_kwargs = calls["transcribe_kwargs"]
    assert isinstance(transcribe_kwargs, list)
    assert len(transcribe_kwargs) == 2
    assert [kwargs["hotwords"] for kwargs in transcribe_kwargs] == [
        "CaptionNest, 初音未来",
        "CaptionNest, 初音未来",
    ]
    assert result.diagnostics is not None
    assert "CaptionNest" not in result.diagnostics.model_dump_json()
    assert "初音未来" not in result.diagnostics.model_dump_json()


def test_provider_rejects_character_valid_cjk_hotwords_over_model_token_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(monkeypatch, calls)
    hotwords = [f"葬送芙莉莲角色{index:02d}" for index in range(50)]
    settings = ASRSettings(hotwords=hotwords)

    assert len(settings.hotwords) == 50
    assert sum(len(item) for item in settings.hotwords) <= 512
    with pytest.raises(ValueError, match="超过 223 个 Token") as exc_info:
        FasterWhisperProvider().transcribe(
            tmp_path / "video.mp4",
            language="ja",
            settings=settings,
        )

    error = str(exc_info.value)
    assert hotwords[0] not in error
    assert hotwords[-1] not in error
    assert "transcribe_kwargs" not in calls


@pytest.mark.parametrize(
    "output_mode",
    [ASROutputMode.WORD_RESEGMENTED, ASROutputMode.CHUNK_SEGMENTS],
)
def test_provider_normalizes_silence_boundaries_for_both_output_modes(
    output_mode: ASROutputMode,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(
        monkeypatch,
        calls,
        timestamp_intrusion=True,
    )

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="zh",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=False,
            timestamp_normalization=True,
            output_mode=output_mode,
        ),
    )

    assert calls["vad_count"] == 1
    assert len(calls["transcribe_kwargs"]) == 1  # type: ignore[arg-type]
    assert [(item.id, item.text) for item in result.segments] == [
        ("seg-000001", "疑似片段")
    ]
    assert [(item.start_ms, item.end_ms) for item in result.segments] == [
        (4_000, 8_000)
    ]
    assert result.diagnostics is not None
    summary = result.diagnostics.summary
    assert summary.timestamp_normalization_status == "applied"
    assert summary.timestamp_word_boundary_shift_count == 2
    assert summary.timestamp_segment_boundary_shift_count == 2
    assert summary.timestamp_boundary_shift_abs_total_samples == 6_400
    assert summary.timestamp_fallback_to_original_count == 0


@pytest.mark.parametrize(
    "output_mode",
    [ASROutputMode.WORD_RESEGMENTED, ASROutputMode.CHUNK_SEGMENTS],
)
@pytest.mark.parametrize(
    (
        "audio_samples",
        "timestamp_interval",
        "expected_status",
        "expected_fallback_count",
    ),
    [
        (30 * 16_000, (0.1, 0.15), "applied", 0),
        (800, (0.01, 0.04), "fallback", 1),
    ],
)
def test_provider_repairs_short_timestamps_or_reports_fallback_for_both_modes(
    output_mode: ASROutputMode,
    audio_samples: int,
    timestamp_interval: tuple[float, float],
    expected_status: str,
    expected_fallback_count: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(
        monkeypatch,
        calls,
        timestamp_interval=timestamp_interval,
        audio_samples=audio_samples,
    )

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="zh",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=False,
            timestamp_normalization=True,
            output_mode=output_mode,
        ),
    )

    assert calls["vad_count"] == 1
    assert len(result.segments) == 1
    assert result.segments[0].id == "seg-000001"
    if expected_status == "applied":
        assert result.segments[0].end_ms - result.segments[0].start_ms >= 100

    assert result.diagnostics is not None
    summary = result.diagnostics.summary
    assert summary.timestamp_normalization_status == expected_status
    assert (
        summary.timestamp_fallback_to_original_count == expected_fallback_count
    )
    if expected_status == "applied":
        assert summary.timestamp_word_boundary_shift_count == 2
        assert summary.timestamp_segment_boundary_shift_count == 2
    else:
        assert summary.timestamp_unsafe_adjustment_count >= 1
        assert summary.timestamp_word_boundary_shift_count == 0
        assert summary.timestamp_segment_boundary_shift_count == 0


@pytest.mark.parametrize(
    "output_mode",
    [ASROutputMode.WORD_RESEGMENTED, ASROutputMode.CHUNK_SEGMENTS],
)
def test_provider_clips_crossing_retries_and_preserves_sandwiched_neighbor(
    output_mode: ASROutputMode,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {"transcription_count": 0}

    class FakeAudio:
        def __len__(self) -> int:
            return 30 * 16_000

        def __getitem__(self, key):  # type: ignore[no-untyped-def]
            calls.setdefault("slices", []).append((key.start, key.stop))  # type: ignore[union-attr]
            return self

    class FakeModel:
        def __init__(self, model, **kwargs):  # type: ignore[no-untyped-def]
            calls["model"] = model

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = int(calls["transcription_count"])
            calls["transcription_count"] = index + 1
            if index == 0:
                segments = [
                    SimpleNamespace(
                        text="bad-a",
                        start=4.0,
                        end=5.0,
                        avg_logprob=-1.6,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="bad-a", start=4.0, end=5.0),
                        ],
                    ),
                    SimpleNamespace(
                        text="middle",
                        start=6.0,
                        end=7.0,
                        avg_logprob=-0.1,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="middle", start=6.0, end=7.0),
                        ],
                    ),
                    SimpleNamespace(
                        text="bad-b",
                        start=8.0,
                        end=9.0,
                        avg_logprob=-1.6,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="bad-b", start=8.0, end=9.0),
                        ],
                    ),
                ]
            elif index == 1:
                segments = [
                    SimpleNamespace(
                        text="good-amiddle",
                        start=1.5,
                        end=5.5,
                        avg_logprob=-0.1,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="good-a", start=2.0, end=3.0),
                            SimpleNamespace(word="middle", start=4.0, end=5.0),
                        ],
                    )
                ]
            else:
                segments = [
                    SimpleNamespace(
                        text="middlegood-b",
                        start=0.0,
                        end=3.0,
                        avg_logprob=-0.1,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="middle", start=0.0, end=1.0),
                            SimpleNamespace(word="good-b", start=2.0, end=3.0),
                        ],
                    )
                ]
            return iter(segments), SimpleNamespace(
                language=kwargs["language"],
                language_probability=0.9,
            )

    faster_whisper_module = ModuleType("faster_whisper")
    faster_whisper_module.WhisperModel = FakeModel  # type: ignore[attr-defined]
    audio_module = ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda path, sampling_rate: FakeAudio()  # type: ignore[attr-defined]
    vad_module = ModuleType("faster_whisper.vad")
    vad_module.VadOptions = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    vad_module.get_speech_timestamps = (  # type: ignore[attr-defined]
        lambda audio, **kwargs: [
            {"start": 4 * 16_000, "end": 9 * 16_000},
        ]
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad_module)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="en",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=output_mode,
        ),
    )

    assert calls["slices"] == [
        (0, 30 * 16_000),
        (2 * 16_000, 7 * 16_000),
        (6 * 16_000, 11 * 16_000),
    ]
    assert [segment.text for segment in result.segments] == [
        "good-a",
        "middle",
        "good-b",
    ]
    assert [segment.start_ms for segment in result.segments] == [4_000, 6_000, 8_000]
    assert result.diagnostics is not None
    assert [retry.candidate_ids for retry in result.diagnostics.retries] == [
        ("candidate-chunk-000000-segment-000000",),
        ("candidate-chunk-000000-segment-000002",),
    ]
    assert [retry.status for retry in result.diagnostics.retries] == [
        "selected_retry",
        "selected_retry",
    ]
    assert all(
        retry.core.start_sample <= diagnostics.interval.start_sample
        and diagnostics.interval.end_sample <= retry.core.end_sample
        for retry in result.diagnostics.retries
        for diagnostics in result.diagnostics.segments
        if diagnostics.candidate_id.startswith(f"candidate-{retry.request_id}")
    )


@pytest.mark.parametrize(
    "output_mode",
    [ASROutputMode.WORD_RESEGMENTED, ASROutputMode.CHUNK_SEGMENTS],
)
@pytest.mark.parametrize(
    ("fill_gap", "expected_status", "expected_texts"),
    [
        (False, "selected_initial", ["first", "next"]),
        (True, "selected_retry", ["filled", "next"]),
    ],
)
def test_provider_only_selects_speech_gap_retry_when_coverage_improves(
    output_mode: ASROutputMode,
    fill_gap: bool,
    expected_status: str,
    expected_texts: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {"transcription_count": 0}

    class FakeAudio:
        def __len__(self) -> int:
            return 30 * 16_000

        def __getitem__(self, key):  # type: ignore[no-untyped-def]
            calls.setdefault("slices", []).append((key.start, key.stop))  # type: ignore[union-attr]
            return self

    class FakeModel:
        def __init__(self, model, **kwargs):  # type: ignore[no-untyped-def]
            calls["model"] = model

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = int(calls["transcription_count"])
            calls["transcription_count"] = index + 1
            if index == 0:
                segments = [
                    SimpleNamespace(
                        text="first",
                        start=4.0,
                        end=5.0,
                        avg_logprob=-0.2,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="first", start=4.0, end=5.0),
                        ],
                    ),
                    SimpleNamespace(
                        text="next",
                        start=8.0,
                        end=9.0,
                        avg_logprob=-0.2,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(word="next", start=8.0, end=9.0),
                        ],
                    ),
                ]
            else:
                retry_end = 7.0 if fill_gap else 4.0
                retry_text = "filled" if fill_gap else "first"
                segments = [
                    SimpleNamespace(
                        text=retry_text,
                        start=3.0,
                        end=retry_end,
                        avg_logprob=-0.2,
                        no_speech_prob=0.1,
                        compression_ratio=1.2,
                        temperature=0.0,
                        words=[
                            SimpleNamespace(
                                word=retry_text,
                                start=3.0,
                                end=retry_end,
                            ),
                        ],
                    )
                ]
            return iter(segments), SimpleNamespace(
                language=kwargs["language"],
                language_probability=0.9,
            )

    faster_whisper_module = ModuleType("faster_whisper")
    faster_whisper_module.WhisperModel = FakeModel  # type: ignore[attr-defined]
    audio_module = ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda path, sampling_rate: FakeAudio()  # type: ignore[attr-defined]
    vad_module = ModuleType("faster_whisper.vad")
    vad_module.VadOptions = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    vad_module.get_speech_timestamps = (  # type: ignore[attr-defined]
        lambda audio, **kwargs: [
            {"start": 4 * 16_000, "end": 9 * 16_000},
        ]
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad_module)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="en",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=output_mode,
        ),
    )

    assert calls["slices"] == [
        (0, 30 * 16_000),
        (1 * 16_000, 11 * 16_000),
    ]
    assert result.diagnostics is not None
    retry = result.diagnostics.retries[0]
    assert retry.status == expected_status, retry.model_dump_json()
    assert [segment.text for segment in result.segments] == expected_texts
    assert retry.reasons == ("speech_gap",)
    assert retry.initial_score == pytest.approx(retry.retry_score)
    assert result.segments[0].end_ms == (8_000 if fill_gap else 5_000)


def test_provider_disabled_retry_preserves_single_pass_without_vad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(monkeypatch, calls)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="ja",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=False,
            output_mode=ASROutputMode.CHUNK_SEGMENTS,
        ),
    )

    assert len(calls["transcribe_kwargs"]) == 1  # type: ignore[arg-type]
    assert calls.get("vad_count", 0) == 0
    assert [segment.text for segment in result.segments] == ["疑似片段"]
    assert result.diagnostics is not None
    assert result.diagnostics.audio.vad_status == "unavailable"
    assert result.diagnostics.summary.retry_candidate_count == 0
    assert result.diagnostics.summary.retry_request_count == 0
    assert result.diagnostics.retries == ()


def test_provider_retry_failure_keeps_initial_without_raw_error_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(monkeypatch, calls, fail_retry=True)

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="ja",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=ASROutputMode.CHUNK_SEGMENTS,
        ),
    )

    assert [segment.text for segment in result.segments] == ["疑似片段"]
    assert result.diagnostics is not None
    assert result.diagnostics.summary.retry_failed_count == 1
    assert result.diagnostics.summary.retry_initial_selected_count == 0
    assert result.diagnostics.retries[0].status == "failed"
    assert "raw provider detail" not in result.diagnostics.model_dump_json()


def test_provider_empty_retry_keeps_near_silence_with_any_vad_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(
        monkeypatch,
        calls,
        empty_retry=True,
        near_silence=True,
    )

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="ja",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=ASROutputMode.CHUNK_SEGMENTS,
        ),
    )

    assert [segment.text for segment in result.segments] == ["疑似片段"]
    assert result.diagnostics is not None
    assert result.diagnostics.summary.retry_selected_count == 0
    assert result.diagnostics.summary.output_segment_count == 1
    assert result.diagnostics.retries[0].status == "selected_initial"
    assert result.diagnostics.retries[0].reasons == ("speech_conflict",)


def test_provider_empty_retry_removes_only_vad_confirmed_silence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    _install_selective_retry_fakes(
        monkeypatch,
        calls,
        empty_retry=True,
        confirmed_silence=True,
    )

    result = FasterWhisperProvider().transcribe(
        tmp_path / "video.mp4",
        language="ja",
        settings=ASRSettings(
            dynamic_chunking=False,
            selective_retry=True,
            output_mode=ASROutputMode.CHUNK_SEGMENTS,
        ),
    )

    assert result.segments == []
    assert result.diagnostics is not None
    assert result.diagnostics.summary.retry_selected_count == 1
    assert result.diagnostics.summary.output_segment_count == 0
    assert result.diagnostics.retries[0].status == "selected_empty"


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
            self.hf_tokenizer = SimpleNamespace(
                encode=lambda text, add_special_tokens=False: SimpleNamespace(
                    ids=range(len(text))
                )
            )
            self.max_length = 448

        def transcribe(self, audio, **kwargs):  # type: ignore[no-untyped-def]
            index = int(calls["transcription_count"])
            calls["transcription_count"] = index + 1
            calls.setdefault("transcribe_kwargs", []).append(kwargs)  # type: ignore[union-attr]
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
        settings=ASRSettings(
            output_mode=output_mode,
            hotwords=["CaptionNest", "初音未来"],
        ),
    )

    assert calls["vad_count"] == 1
    assert calls["transcription_count"] == 2
    assert [
        kwargs["hotwords"] for kwargs in calls["transcribe_kwargs"]  # type: ignore[index]
    ] == ["CaptionNest, 初音未来", "CaptionNest, 初音未来"]
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
        settings=ASRSettings(
            output_mode=ASROutputMode.CHUNK_SEGMENTS,
            timestamp_normalization=True,
        ),
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
    assert (
        result.diagnostics.summary.timestamp_normalization_status
        == "unavailable"
    )


def test_output_mode_defaults_to_word_resegmentation() -> None:
    assert ASRSettings().output_mode == ASROutputMode.WORD_RESEGMENTED
    assert ASRSettings().dynamic_chunking is True
    assert ASRSettings().timestamp_normalization is False
    assert ASRSettings().selective_retry is True


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
