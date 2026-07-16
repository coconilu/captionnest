from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from ..models import ModelUsageSummary, TargetLanguage, TranslatedItem, TranslationItem
from .base import ModelUsageCallback, TranslationProvider, emit_model_usage
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
        provider_name: str = "openai_compatible",
        timeout_seconds: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = chat_completions_url(endpoint)
        self.model = model
        self.provider_name = provider_name
        self._api_key = api_key.strip() if api_key else None
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def translate(
        self,
        items: Sequence[TranslationItem],
        *,
        source_language: str,
        target_language: TargetLanguage,
        on_usage: ModelUsageCallback | None = None,
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
                response = await self._post(
                    client,
                    headers=headers,
                    payload=payload,
                    on_usage=on_usage,
                )
                if response.status_code in {400, 422}:
                    # Some older OpenAI-compatible servers reject response_format.
                    fallback_payload = dict(payload)
                    fallback_payload.pop("response_format", None)
                    response = await self._post(
                        client,
                        headers=headers,
                        payload=fallback_payload,
                        on_usage=on_usage,
                    )
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

    async def _post(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        on_usage: ModelUsageCallback | None,
    ) -> httpx.Response:
        try:
            response = await client.post(self.url, headers=headers, json=payload)
        except httpx.HTTPError:
            emit_model_usage(on_usage, self._unavailable_usage())
            raise
        emit_model_usage(on_usage, self._usage_from_response(response))
        return response

    def _unavailable_usage(self) -> ModelUsageSummary:
        return ModelUsageSummary(
            provider=self.provider_name,
            model=self.model,
            request_count=1,
            source="unavailable",
            complete=False,
        )

    def _usage_from_response(self, response: httpx.Response) -> ModelUsageSummary:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return self._unavailable_usage()
        if not isinstance(payload, Mapping) or not isinstance(payload.get("usage"), Mapping):
            return self._unavailable_usage()

        usage = payload["usage"]
        assert isinstance(usage, Mapping)
        input_tokens = _first_token_count(usage, "prompt_tokens", "input_tokens")
        output_tokens = _first_token_count(usage, "completion_tokens", "output_tokens")
        total_tokens = _first_token_count(usage, "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        prompt_details = usage.get("prompt_tokens_details")
        completion_details = usage.get("completion_tokens_details")
        cached_input_tokens = _first_token_count(usage, "cached_input_tokens", "cached_tokens")
        if cached_input_tokens is None and isinstance(prompt_details, Mapping):
            cached_input_tokens = _first_token_count(prompt_details, "cached_tokens")
        reasoning_tokens = _first_token_count(
            usage,
            "reasoning_tokens",
            "reasoning_output_tokens",
        )
        if reasoning_tokens is None and isinstance(completion_details, Mapping):
            reasoning_tokens = _first_token_count(completion_details, "reasoning_tokens")

        reported = any(
            value is not None
            for value in (
                input_tokens,
                output_tokens,
                total_tokens,
                cached_input_tokens,
                reasoning_tokens,
            )
        )
        if not reported:
            return self._unavailable_usage()
        return ModelUsageSummary(
            provider=self.provider_name,
            model=self.model,
            request_count=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            source="provider",
            complete=(
                input_tokens is not None
                and output_tokens is not None
                and total_tokens is not None
            ),
        )


def _first_token_count(payload: Mapping[object, object], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None
