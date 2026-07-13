from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from ..models import TranslatedItem, TranslationItem
from ..subtitles import chunk_translation_items, validate_translation_integrity

TranslationProgress = Callable[[int, int], None]


class TranslationProvider(ABC):
    @abstractmethod
    async def translate(
        self, items: Sequence[TranslationItem], *, source_language: str
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
        on_progress: TranslationProgress | None = None,
    ) -> list[TranslatedItem]:
        chunks = chunk_translation_items(
            items, max_items=self._max_items, max_chars=self._max_chars
        )
        translated: list[TranslatedItem] = []
        for index, chunk in enumerate(chunks, start=1):
            result = await self._provider.translate(chunk, source_language=source_language)
            validate_translation_integrity(chunk, result)
            translated.extend(result)
            if on_progress:
                on_progress(index, len(chunks))
        validate_translation_integrity(items, translated)
        return translated

