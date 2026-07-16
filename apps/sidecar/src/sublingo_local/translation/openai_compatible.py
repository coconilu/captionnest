from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

import httpx

from ..models import TargetLanguage, TranslatedItem, TranslationItem
from .base import TranslationProvider
from .common import build_translation_prompt, parse_translation_json


def chat_completions_url(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Endpoint 必须是 http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("Endpoint 不得包含用户名或密码")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return endpoint + "/chat/completions"


class OpenAICompatibleProvider(TranslationProvider):
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = chat_completions_url(endpoint)
        self.model = model
        self._api_key = api_key.strip() if api_key else None
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def translate(
        self,
        items: Sequence[TranslationItem],
        *,
        source_language: str,
        target_language: TargetLanguage,
    ) -> list[TranslatedItem]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "只执行字幕翻译，并且只输出有效 JSON。"},
                {
                    "role": "user",
                    "content": build_translation_prompt(items, source_language, target_language),
                },
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(self.url, headers=headers, json=payload)
                if response.status_code in {400, 422}:
                    # Some older OpenAI-compatible servers reject response_format.
                    fallback_payload = dict(payload)
                    fallback_payload.pop("response_format", None)
                    response = await client.post(self.url, headers=headers, json=fallback_payload)
        except httpx.TimeoutException as exc:
            raise RuntimeError("翻译接口请求超时") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("无法连接翻译接口") from exc
        if not response.is_success:
            # Response bodies are intentionally omitted to avoid logging reflected credentials.
            raise RuntimeError(f"翻译接口请求失败（HTTP {response.status_code}）")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("翻译接口响应格式不兼容") from exc
        if not isinstance(content, str):
            raise RuntimeError("翻译接口没有返回文本内容")
        return parse_translation_json(content)
