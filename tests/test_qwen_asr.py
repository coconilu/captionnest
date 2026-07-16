import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY

import pytest

from sublingo_local.asr.qwen3_asr import (
    Qwen3ASRProvider,
    _aligned_items_with_punctuation,
    _alignment_quality_issue,
    aligned_items_to_segments,
)
from sublingo_local.models import ASRSettings


def _item(text: str, start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(text=text, start_time=start, end_time=end)


def test_qwen_timestamp_items_become_readable_japanese_cues() -> None:
    segments = aligned_items_to_segments(
        [
            _item("今", 0.0, 0.2),
            _item("日", 0.2, 0.4),
            _item("は", 0.4, 0.6),
            _item("晴", 0.6, 0.8),
            _item("れ", 0.8, 1.0),
            _item("。", 1.0, 1.1),
            _item("次", 2.2, 2.4),
            _item("です", 2.4, 2.8),
        ],
        language="Japanese",
    )

    assert [segment.text for segment in segments] == ["今日は晴れ。", "次です"]
    assert [(segment.start_ms, segment.end_ms) for segment in segments] == [
        (0, 1_100),
        (2_200, 2_800),
    ]


def test_qwen_timestamp_items_preserve_latin_word_spacing() -> None:
    segments = aligned_items_to_segments(
        [_item("Hello", 0, 0.4), _item(",", 0.4, 0.5), _item("world", 0.5, 1.0)],
        language="English",
    )

    assert [segment.text for segment in segments] == ["Hello, world"]


def test_qwen_alignment_keeps_zero_length_tokens_and_restores_punctuation() -> None:
    items = _aligned_items_with_punctuation(
        [_item("今日", 1.0, 1.2), _item("は", 1.2, 1.2), _item("晴れ", 1.3, 1.6)],
        transcript="今日は晴れ。",
        offset_seconds=10.0,
    )
    segments = aligned_items_to_segments(items, language="Japanese")

    assert [segment.text for segment in segments] == ["今日は晴れ。"]
    assert (segments[0].start_ms, segments[0].end_ms) == (11_000, 11_600)


def test_qwen_zero_length_standalone_cue_gets_readable_minimum_duration() -> None:
    segments = aligned_items_to_segments(
        [_item("え", 1.0, 1.0), _item("はい。", 2.0, 2.3)],
        language="Japanese",
    )

    assert [(item.start_ms, item.end_ms) for item in segments] == [
        (1_000, 1_400),
        (2_000, 2_400),
    ]


def test_qwen_alignment_quality_rejects_collapsed_timestamps() -> None:
    collapsed = [_item(character, 12.0, 12.0) for character in "这是一段完全坍缩的时间戳"]

    assert "没有有效时长" in (_alignment_quality_issue(collapsed) or "")
    assert _alignment_quality_issue(
        [_item("今日", 1.0, 1.2), _item("は", 1.2, 1.2), _item("晴れ", 1.3, 1.6)]
    ) is None


def test_qwen_alignment_quality_rejects_one_word_spanning_many_seconds() -> None:
    assert "单个词跨越" in (_alignment_quality_issue([_item("うん", 1, 25)]) or "")


def test_qwen_provider_uses_local_bundle_and_lazy_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}
    asr_path = tmp_path / "asr"
    aligner_path = tmp_path / "aligner"

    class FakeModel:
        @classmethod
        def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
            calls["path"] = path
            calls["load_kwargs"] = kwargs
            return cls()

        def transcribe(self, **kwargs):  # type: ignore[no-untyped-def]
            calls["transcribe_kwargs"] = kwargs
            return [
                SimpleNamespace(
                    language="Japanese",
                    time_stamps=SimpleNamespace(
                        items=[_item("こんにちは", 0.1, 0.9), _item("。", 0.9, 1.0)]
                    ),
                )
            ]

    fake_torch = SimpleNamespace(
        bfloat16="bf16",
        float32="fp32",
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=FakeModel))
    manager = SimpleNamespace(
        resolve_installed_components=lambda model: {"asr": asr_path, "aligner": aligner_path}
    )
    monkeypatch.setattr(
        Qwen3ASRProvider,
        "_decode_to_mono_16k",
        staticmethod(lambda path, *, on_progress: (object(), 1.25)),
    )
    monkeypatch.setattr(
        "sublingo_local.asr.qwen3_asr._split_for_alignment",
        lambda audio: [(audio, 0.0)],
    )
    progress: list[float] = []

    result = Qwen3ASRProvider(manager).transcribe(  # type: ignore[arg-type]
        tmp_path / "video.mp4",
        language="auto",
        settings=ASRSettings(
            provider="qwen3_asr",
            model="qwen3-asr-1.7b",
            device="cuda",
        ),
        on_progress=progress.append,
    )

    assert calls["path"] == str(asr_path)
    assert calls["load_kwargs"] == {
        "dtype": "bf16",
        "device_map": "cuda:0",
        "max_inference_batch_size": 1,
        "max_new_tokens": 1024,
        "forced_aligner": str(aligner_path),
        "forced_aligner_kwargs": {"dtype": "bf16", "device_map": "cuda:0"},
    }
    assert calls["transcribe_kwargs"] == {
        "audio": (ANY, 16_000),
        "language": None,
        "return_time_stamps": True,
    }
    assert result.language == "ja"
    assert result.duration_seconds == 1.25
    assert result.segments[0].text == "こんにちは。"
    assert progress[-1] == 1.0


def test_qwen_provider_ignores_silent_chunk_language_when_voting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeModel:
        calls = 0

        @classmethod
        def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
            return cls()

        def transcribe(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return [SimpleNamespace(language="", text="", time_stamps=None)]
            return [
                SimpleNamespace(
                    language="Japanese",
                    text="こんにちは。",
                    time_stamps=SimpleNamespace(items=[_item("こんにちは", 0.1, 0.9)]),
                )
            ]

    fake_torch = SimpleNamespace(
        bfloat16="bf16",
        float32="fp32",
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=FakeModel))
    monkeypatch.setattr(
        Qwen3ASRProvider,
        "_decode_to_mono_16k",
        staticmethod(lambda path, *, on_progress: (object(), 2.0)),
    )
    monkeypatch.setattr(
        "sublingo_local.asr.qwen3_asr._split_for_alignment",
        lambda audio: [(audio, 0.0), (audio, 1.0)],
    )
    manager = SimpleNamespace(
        resolve_installed_components=lambda model: {
            "asr": tmp_path / "asr",
            "aligner": tmp_path / "aligner",
        }
    )

    result = Qwen3ASRProvider(manager).transcribe(  # type: ignore[arg-type]
        tmp_path / "video.mp4",
        language="auto",
        settings=ASRSettings(
            provider="qwen3_asr",
            model="qwen3-asr-1.7b",
            device="cuda",
        ),
    )

    assert result.language == "ja"
    assert result.segments[0].start_ms == 1_100


def test_qwen_provider_locks_language_after_first_reliable_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    languages: list[object] = []

    class FakeModel:
        @classmethod
        def from_pretrained(cls, path, **kwargs):  # type: ignore[no-untyped-def]
            return cls()

        def transcribe(self, **kwargs):  # type: ignore[no-untyped-def]
            languages.append(kwargs["language"])
            return [
                SimpleNamespace(
                    language="Japanese",
                    text="はい。",
                    time_stamps=SimpleNamespace(items=[_item("はい", 0.1, 0.5)]),
                )
            ]

    fake_torch = SimpleNamespace(
        bfloat16="bf16",
        float32="fp32",
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=FakeModel))
    monkeypatch.setattr(
        Qwen3ASRProvider,
        "_decode_to_mono_16k",
        staticmethod(lambda path, *, on_progress: (object(), 2.0)),
    )
    monkeypatch.setattr(
        "sublingo_local.asr.qwen3_asr._split_for_alignment",
        lambda audio: [(audio, 0.0), (audio, 1.0)],
    )
    manager = SimpleNamespace(
        resolve_installed_components=lambda model: {
            "asr": tmp_path / "asr",
            "aligner": tmp_path / "aligner",
        }
    )

    Qwen3ASRProvider(manager).transcribe(  # type: ignore[arg-type]
        tmp_path / "video.mp4",
        language="auto",
        settings=ASRSettings(
            provider="qwen3_asr",
            model="qwen3-asr-1.7b",
            device="cuda",
        ),
    )

    assert languages == [None, "Japanese"]


def test_qwen_settings_reject_mismatched_provider_and_model() -> None:
    with pytest.raises(ValueError, match="Qwen3-ASR 模型必须"):
        ASRSettings(model="qwen3-asr-1.7b")
    with pytest.raises(ValueError, match="只支持模型"):
        ASRSettings(provider="qwen3_asr", model="large-v3")
