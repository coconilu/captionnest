import asyncio
import json
from pathlib import Path

import httpx
import pytest

from sublingo_local.models import TranslatedItem, TranslationItem
from sublingo_local.subtitles import SubtitleIntegrityError
from sublingo_local.translation.base import TranslationProvider, TranslationService
from sublingo_local.translation.codex import CodexSparkProvider
from sublingo_local.translation.openai_compatible import (
    OpenAICompatibleProvider,
    chat_completions_url,
)


class EchoProvider(TranslationProvider):
    async def translate(self, items, *, source_language):  # type: ignore[no-untyped-def]
        return [TranslatedItem(id=item.id, translated_text=f"中:{item.text}") for item in items]


class MissingProvider(TranslationProvider):
    async def translate(self, items, *, source_language):  # type: ignore[no-untyped-def]
        return []


def test_translation_service_chunks_and_validates() -> None:
    items = [TranslationItem(id=str(i), text="hello") for i in range(5)]
    service = TranslationService(EchoProvider(), max_items_per_chunk=2)
    output = asyncio.run(service.translate(items, source_language="en"))
    assert [item.id for item in output] == ["0", "1", "2", "3", "4"]

    invalid = TranslationService(MissingProvider())
    with pytest.raises(SubtitleIntegrityError):
        asyncio.run(invalid.translate(items, source_language="en"))


def test_codex_provider_uses_parameter_array_and_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Result:
        returncode = 0

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        captured["args"] = args
        captured["kwargs"] = kwargs
        output = Path(args[args.index("--output-last-message") + 1])
        output.write_text(
            json.dumps({"items": [{"id": "seg-1", "translated_text": "你好"}]}),
            encoding="utf-8",
        )
        return Result()

    monkeypatch.setattr("sublingo_local.translation.codex.subprocess.run", fake_run)
    provider = CodexSparkProvider()
    result = asyncio.run(
        provider.translate([TranslationItem(id="seg-1", text="hello")], source_language="en")
    )

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert isinstance(args, list)
    assert args[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--ignore-user-config" in args
    assert "--ignore-rules" in args
    disabled_features = [
        args[index + 1] for index, value in enumerate(args) if value == "--disable"
    ]
    assert disabled_features == [
        "shell_tool",
        "computer_use",
        "browser_use",
        "in_app_browser",
        "apps",
        "image_generation",
        "multi_agent",
    ]
    assert "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[-1] == "-"
    assert kwargs["shell"] is False
    assert kwargs["input"].find("hello") >= 0
    assert result == [TranslatedItem(id="seg-1", translated_text="你好")]


def test_openai_compatible_retries_without_response_format() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-value"
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": "1", "translated_text": "早上好"}]},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleProvider(
        endpoint="http://127.0.0.1:1234/v1",
        model="local-model",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(
        provider.translate([TranslationItem(id="1", text="good morning")], source_language="en")
    )
    assert output[0].translated_text == "早上好"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_openai_compatible_does_not_retry_auth_failure() -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(401)

    provider = OpenAICompatibleProvider(
        endpoint="https://example.test/v1",
        model="model",
        api_key="top-secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        asyncio.run(
            provider.translate([TranslationItem(id="1", text="x")], source_language="en")
        )
    assert count == 1
    assert "top-secret" not in str(error.value)


def test_endpoint_validation() -> None:
    assert chat_completions_url("http://localhost:1234/v1") == (
        "http://localhost:1234/v1/chat/completions"
    )
    with pytest.raises(ValueError):
        chat_completions_url("file:///tmp/socket")
    with pytest.raises(ValueError):
        chat_completions_url("https://user:password@example.test/v1")
