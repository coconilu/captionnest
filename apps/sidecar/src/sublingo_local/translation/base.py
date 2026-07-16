from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Sequence

from ..models import TargetLanguage, TranslatedItem, TranslationItem
from ..subtitles import (
    SubtitleIntegrityError,
    chunk_translation_items,
    validate_translation_integrity,
)

TranslationProgress = Callable[[int, int], None]
TranslationRecovery = Callable[[str], None]


def _canonicalize_translation_result(
    source: Sequence[TranslationItem], translated: Sequence[TranslatedItem]
) -> tuple[list[TranslatedItem], str | None]:
    """Restore source order by stable ID and safely discard unrelated extra IDs."""
    source_ids = [item.id for item in source]
    source_id_set = set(source_ids)
    relevant = [item for item in translated if item.id in source_id_set]
    counts = Counter(item.id for item in relevant)
    duplicates = sorted(item_id for item_id, count in counts.items() if count > 1)
    if duplicates:
        raise SubtitleIntegrityError(
            "翻译结果包含重复 ID：" + ", ".join(duplicates)
        )

    translated_by_id = {item.id: item for item in relevant}
    missing = [item_id for item_id in source_ids if item_id not in translated_by_id]
    if missing:
        raise SubtitleIntegrityError(
            "翻译结果 ID 不完整：缺少 " + ", ".join(missing)
        )

    canonical = [translated_by_id[item_id] for item_id in source_ids]
    validate_translation_integrity(source, canonical)

    notes: list[str] = []
    extra = sorted({item.id for item in translated} - source_id_set)
    if extra:
        notes.append("已忽略模型多返回的 ID：" + ", ".join(extra))
    if [item.id for item in relevant] != source_ids:
        notes.append("已按稳定 ID 恢复字幕顺序")
    return canonical, "；".join(notes) or None


class TranslationProvider(ABC):
    @abstractmethod
    async def translate(
        self,
        items: Sequence[TranslationItem],
        *,
        source_language: str,
        target_language: TargetLanguage,
    ) -> list[TranslatedItem]:
        """Translate one stable-ID batch without changing item order."""


class TranslationService:
    def __init__(
        self,
        provider: TranslationProvider,
        *,
        max_items_per_chunk: int = 30,
        max_chars_per_chunk: int = 6_000,
    ) -> None:
        self._provider = provider
        self._max_items = max_items_per_chunk
        self._max_chars = max_chars_per_chunk

    async def translate(
        self,
        items: Sequence[TranslationItem],
        *,
        source_language: str,
        target_language: TargetLanguage,
        on_progress: TranslationProgress | None = None,
        on_recovery: TranslationRecovery | None = None,
    ) -> list[TranslatedItem]:
        chunks = chunk_translation_items(
            items, max_items=self._max_items, max_chars=self._max_chars
        )
        translated: list[TranslatedItem] = []
        for index, chunk in enumerate(chunks, start=1):
            result = await self._translate_chunk(
                chunk,
                source_language=source_language,
                target_language=target_language,
                on_recovery=on_recovery,
            )
            translated.extend(result)
            if on_progress:
                on_progress(index, len(chunks))
        validate_translation_integrity(items, translated)
        return translated

    async def _translate_chunk(
        self,
        chunk: Sequence[TranslationItem],
        *,
        source_language: str,
        target_language: TargetLanguage,
        on_recovery: TranslationRecovery | None,
    ) -> list[TranslatedItem]:
        result = await self._provider.translate(
            chunk,
            source_language=source_language,
            target_language=target_language,
        )
        try:
            canonical, recovery_note = _canonicalize_translation_result(chunk, result)
        except SubtitleIntegrityError as exc:
            if len(chunk) <= 1:
                raise
            midpoint = len(chunk) // 2
            if on_recovery:
                on_recovery(
                    f"翻译结果结构异常，正在将 {len(chunk)} 条字幕拆分重试：{exc}"
                )
            left = await self._translate_chunk(
                chunk[:midpoint],
                source_language=source_language,
                target_language=target_language,
                on_recovery=on_recovery,
            )
            right = await self._translate_chunk(
                chunk[midpoint:],
                source_language=source_language,
                target_language=target_language,
                on_recovery=on_recovery,
            )
            combined = [*left, *right]
            validate_translation_integrity(chunk, combined)
            return combined

        if recovery_note and on_recovery:
            on_recovery(recovery_note)
        return canonical
