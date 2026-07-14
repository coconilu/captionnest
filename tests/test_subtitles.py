from pathlib import Path

import pytest

from sublingo_local.models import SubtitleSegment, TranslatedItem, TranslationItem
from sublingo_local.subtitles import (
    SubtitleIntegrityError,
    apply_translations,
    chunk_translation_items,
    read_srt,
    validate_translation_integrity,
    write_bilingual_srt,
    write_srt,
)


def test_srt_round_trip_uses_bom_and_stable_ids(tmp_path: Path) -> None:
    path = tmp_path / "字幕.srt"
    segments = [
        SubtitleSegment(id="seg-000001", start_ms=123, end_ms=1_999, text="こんにちは"),
        SubtitleSegment(id="seg-000002", start_ms=3_600_001, end_ms=3_602_345, text="Hello\nworld"),
    ]

    write_srt(path, segments)

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in path.read_bytes()
    assert read_srt(path) == segments
    assert not list(tmp_path.glob("*.tmp"))


def test_translation_integrity_requires_same_ids_and_order() -> None:
    source = [TranslationItem(id="a", text="A"), TranslationItem(id="b", text="B")]
    with pytest.raises(SubtitleIntegrityError, match="顺序"):
        validate_translation_integrity(
            source,
            [
                TranslatedItem(id="b", translated_text="乙"),
                TranslatedItem(id="a", translated_text="甲"),
            ],
        )
    with pytest.raises(SubtitleIntegrityError, match="缺少 b"):
        validate_translation_integrity(source, [TranslatedItem(id="a", translated_text="甲")])


def test_chunking_and_apply_translation_preserve_timeline() -> None:
    items = [TranslationItem(id=str(index), text="x" * 4) for index in range(5)]
    chunks = chunk_translation_items(items, max_items=3, max_chars=9)
    assert [len(chunk) for chunk in chunks] == [2, 2, 1]

    segment = SubtitleSegment(id="1", start_ms=100, end_ms=500, text="hello")
    result = apply_translations([segment], [TranslatedItem(id="1", translated_text="你好")])
    assert result[0].text == "你好"
    assert (result[0].start_ms, result[0].end_ms) == (100, 500)


def test_bilingual_srt_writes_source_above_translation_atomically(tmp_path: Path) -> None:
    path = tmp_path / "movie.srt"
    source = [
        SubtitleSegment(id="seg-000001", start_ms=100, end_ms=500, text="Hello, world."),
        SubtitleSegment(id="seg-000002", start_ms=600, end_ms=900, text="Goodbye."),
    ]
    translated = [
        TranslatedItem(id="seg-000001", translated_text="你好，世界。"),
        TranslatedItem(id="seg-000002", translated_text="再见。"),
    ]

    write_bilingual_srt(path, source, translated)

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8-sig") == (
        "1\n"
        "00:00:00,100 --> 00:00:00,500\n"
        "Hello, world.\n"
        "你好，世界。\n\n"
        "2\n"
        "00:00:00,600 --> 00:00:00,900\n"
        "Goodbye.\n"
        "再见。\n"
    )
    assert not list(tmp_path.glob("*.tmp"))


def test_bilingual_srt_validates_ids_before_creating_output(tmp_path: Path) -> None:
    path = tmp_path / "invalid.srt"
    source = [SubtitleSegment(id="a", start_ms=100, end_ms=500, text="Hello")]

    with pytest.raises(SubtitleIntegrityError, match="缺少 a"):
        write_bilingual_srt(
            path,
            source,
            [TranslatedItem(id="b", translated_text="你好")],
        )

    assert not path.exists()
