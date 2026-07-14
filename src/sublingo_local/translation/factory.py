from __future__ import annotations

from ..models import TranslationProviderName, TranslationSettings
from .base import TranslationProvider
from .codex import CodexSparkProvider
from .openai_compatible import OpenAICompatibleProvider


def create_translation_provider(settings: TranslationSettings) -> TranslationProvider:
    if settings.provider == TranslationProviderName.CODEX_SPARK:
        return CodexSparkProvider(
            model=settings.model or "gpt-5.3-codex-spark",
            timeout_seconds=settings.timeout_seconds,
        )
    api_key = settings.api_key.get_secret_value() if settings.api_key else None
    if settings.provider == TranslationProviderName.LM_STUDIO:
        return OpenAICompatibleProvider(
            endpoint=settings.endpoint or "http://127.0.0.1:1234/v1",
            model=settings.model or "",
            api_key=api_key,
            timeout_seconds=settings.timeout_seconds,
        )
    if settings.provider == TranslationProviderName.DEEPSEEK:
        return OpenAICompatibleProvider(
            endpoint=settings.endpoint or "https://api.deepseek.com",
            model=settings.model or "deepseek-v4-flash",
            api_key=api_key,
            timeout_seconds=settings.timeout_seconds,
        )
    raise ValueError(f"不支持的翻译 Provider：{settings.provider}")
