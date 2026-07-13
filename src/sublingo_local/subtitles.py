from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from .models import SubtitleSegment, TranslatedItem, TranslationItem

_TIMESTAMP_RE = re.compile(
    r"^(?P<hours>\d{2,}):(?P<minutes>\d{2}):(?P<seconds>\d{2})[,.](?P<millis>\d{3})$"
)


class SubtitleIntegrityError(ValueError):
    """Raised when a translator changes the stable subtitle ID set."""


def milliseconds_to_srt(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def srt_to_milliseconds(value: str) -> int:
    match = _TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无效的 SRT 时间戳：{value}")
    parts = {key: int(item) for key, item in match.groupdict().items()}
    return (
        parts["hours"] * 3_600_000
        + parts["minutes"] * 60_000
        + parts["seconds"] * 1_000
        + parts["millis"]
    )


def write_srt(path: Path, segments: Sequence[SubtitleSegment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                (
                    str(index),
                    f"{milliseconds_to_srt(segment.start_ms)} --> "
                    f"{milliseconds_to_srt(segment.end_ms)}",
                    segment.text.strip(),
                )
            )
        )
    content = "\n\n".join(blocks) + ("\n" if blocks else "")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def read_srt(path: Path) -> list[SubtitleSegment]:
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if not content:
        return []

    result: list[SubtitleSegment] = []
    for position, block in enumerate(re.split(r"\n{2,}", content), start=1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"第 {position} 个字幕块不完整")
        try:
            display_id = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError(f"第 {position} 个字幕块编号无效") from exc
        if "-->" not in lines[1]:
            raise ValueError(f"第 {position} 个字幕块缺少时间轴")
        start, end = (item.strip() for item in lines[1].split("-->", maxsplit=1))
        result.append(
            SubtitleSegment(
                id=f"seg-{display_id:06d}",
                start_ms=srt_to_milliseconds(start),
                end_ms=srt_to_milliseconds(end),
                text="\n".join(lines[2:]).strip(),
            )
        )
    return result


def segments_to_translation_items(segments: Sequence[SubtitleSegment]) -> list[TranslationItem]:
    return [TranslationItem(id=segment.id, text=segment.text) for segment in segments]


def apply_translations(
    segments: Sequence[SubtitleSegment], translated: Sequence[TranslatedItem]
) -> list[SubtitleSegment]:
    validate_translation_integrity(segments_to_translation_items(segments), translated)
    by_id = {item.id: item.translated_text.strip() for item in translated}
    return [segment.model_copy(update={"text": by_id[segment.id]}) for segment in segments]


def chunk_translation_items(
    items: Sequence[TranslationItem], *, max_items: int = 30, max_chars: int = 6_000
) -> list[list[TranslationItem]]:
    if max_items < 1 or max_chars < 1:
        raise ValueError("分块限制必须大于零")
    chunks: list[list[TranslationItem]] = []
    current: list[TranslationItem] = []
    current_chars = 0
    for item in items:
        item_chars = len(item.text)
        if current and (len(current) >= max_items or current_chars + item_chars > max_chars):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)
    return chunks


def validate_translation_integrity(
    source: Sequence[TranslationItem], translated: Sequence[TranslatedItem]
) -> None:
    source_ids = [item.id for item in source]
    translated_ids = [item.id for item in translated]
    if len(set(source_ids)) != len(source_ids):
        raise SubtitleIntegrityError("源字幕包含重复 ID")
    if len(set(translated_ids)) != len(translated_ids):
        raise SubtitleIntegrityError("翻译结果包含重复 ID")
    if source_ids != translated_ids:
        missing = sorted(set(source_ids) - set(translated_ids))
        extra = sorted(set(translated_ids) - set(source_ids))
        details: list[str] = []
        if missing:
            details.append(f"缺少 {', '.join(missing)}")
        if extra:
            details.append(f"多出 {', '.join(extra)}")
        if not missing and not extra:
            details.append("ID 顺序被改变")
        raise SubtitleIntegrityError("翻译结果 ID 不完整：" + "；".join(details))
    empty = [item.id for item in translated if not item.translated_text.strip()]
    if empty:
        raise SubtitleIntegrityError(f"翻译结果为空：{', '.join(empty)}")


def ensure_unique_ids(items: Iterable[TranslationItem]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise SubtitleIntegrityError(f"重复字幕 ID：{item.id}")
        seen.add(item.id)
