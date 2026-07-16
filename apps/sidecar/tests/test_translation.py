import asyncio
import json
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from sublingo_local.models import TargetLanguage, TranslatedItem, TranslationItem
from sublingo_local.subtitles import SubtitleIntegrityError
from sublingo_local.translation.base import TranslationProvider, TranslationService
from sublingo_local.translation.codex import CodexSparkProvider
from sublingo_local.translation.common import build_translation_prompt
from sublingo_local.translation.openai_compatible import (
    OpenAICompatibleProvider,
    chat_completions_url,
)


class EchoProvider(TranslationProvider):
    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language
    ):
        assert source_language == "en"
        assert target_language == TargetLanguage.ZH_CN
        return [TranslatedItem(id=item.id, translated_text=f"中:{item.text}") for item in items]


class MissingProvider(TranslationProvider):
    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language
    ):
        return []


class ExtraAndReorderedProvider(TranslationProvider):
    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language
    ):
        translated = [
            TranslatedItem(id=item.id, translated_text=f"中:{item.text}")
            for item in reversed(items)
        ]
        translated.append(TranslatedItem(id="seg-extra", translated_text="不应使用"))
        return translated


class SplitRequiredProvider(TranslationProvider):
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language
    ):
        self.batch_sizes.append(len(items))
        translated = [
            TranslatedItem(id=item.id, translated_text=f"中:{item.text}") for item in items
        ]
        return translated if len(items) == 1 else translated[:-1]


@pytest.mark.parametrize(
    ("target_language", "display_name"),
    [
        (TargetLanguage.ZH_CN, "简体中文"),
        (TargetLanguage.EN, "英语"),
        (TargetLanguage.KO, "韩语"),
    ],
)
def test_translation_prompt_names_requested_target_language(
    target_language: TargetLanguage, display_name: str
) -> None:
    prompt = build_translation_prompt(
        [TranslationItem(id="1", text="こんにちは")],
        "ja",
        target_language,
    )

    assert f"翻译成自然、简洁的{display_name}" in prompt
    assert f'"target_language":"{target_language.value}"' in prompt


def test_translation_service_chunks_and_validates() -> None:
    items = [TranslationItem(id=str(i), text="hello") for i in range(5)]
    service = TranslationService(EchoProvider(), max_items_per_chunk=2)
    output = asyncio.run(
        service.translate(
            items,
            source_language="en",
            target_language=TargetLanguage.ZH_CN,
        )
    )
    assert [item.id for item in output] == ["0", "1", "2", "3", "4"]

    invalid = TranslationService(MissingProvider())
    with pytest.raises(SubtitleIntegrityError):
        asyncio.run(
            invalid.translate(
                items,
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
            )
        )


def test_translation_service_canonicalizes_extra_and_reordered_ids() -> None:
    items = [TranslationItem(id=f"seg-{index}", text=f"text-{index}") for index in range(3)]
    recoveries: list[str] = []

    output = asyncio.run(
        TranslationService(ExtraAndReorderedProvider()).translate(
            items,
            source_language="en",
            target_language=TargetLanguage.ZH_CN,
            on_recovery=recoveries.append,
        )
    )

    assert [item.id for item in output] == ["seg-0", "seg-1", "seg-2"]
    assert any("seg-extra" in message for message in recoveries)
    assert any("恢复字幕顺序" in message for message in recoveries)


def test_translation_service_splits_invalid_batches_until_ids_are_complete() -> None:
    items = [TranslationItem(id=f"seg-{index}", text=f"text-{index}") for index in range(4)]
    provider = SplitRequiredProvider()
    recoveries: list[str] = []
    progress: list[tuple[int, int]] = []

    output = asyncio.run(
        TranslationService(provider).translate(
            items,
            source_language="en",
            target_language=TargetLanguage.ZH_CN,
            on_progress=lambda done, total: progress.append((done, total)),
            on_recovery=recoveries.append,
        )
    )

    assert [item.id for item in output] == ["seg-0", "seg-1", "seg-2", "seg-3"]
    assert provider.batch_sizes == [4, 2, 1, 1, 2, 1, 1]
    assert progress == [(1, 1)]
    assert len(recoveries) == 3
    assert all("拆分重试" in message for message in recoveries)


def test_codex_provider_uses_parameter_array_and_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, args: list[str]) -> None:
            self.args = args
            self.returncode: int | None = None

        def communicate(self, data: bytes) -> tuple[None, None]:
            captured["input"] = data
            output = Path(self.args[self.args.index("--output-last-message") + 1])
            output.write_text(
                json.dumps({"items": [{"id": "seg-1", "translated_text": "你好"}]}),
                encoding="utf-8",
            )
            self.returncode = 0
            return None, None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self) -> int:
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return FakeProcess(list(args))

    async def unsupported_async_subprocess(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unsupported_async_subprocess)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    provider = CodexSparkProvider()
    result = asyncio.run(
        provider.translate(
            [TranslationItem(id="seg-1", text="hello")],
            source_language="en",
            target_language=TargetLanguage.ZH_CN,
        )
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
    assert "shell" not in kwargs
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    prompt = captured["input"].decode("utf-8")
    assert "hello" in prompt
    assert '"target_language":"zh-CN"' in prompt
    assert result == [TranslatedItem(id="seg-1", translated_text="你好")]


class HangingCodexProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.started = threading.Event()
        self.released = threading.Event()
        self.killed = False
        self.waited = False

    def communicate(self, data: bytes) -> tuple[None, None]:
        assert data
        self.started.set()
        self.released.wait(timeout=2)
        return None, None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.released.set()

    def wait(self) -> int:
        self.waited = True
        self.released.wait(timeout=2)
        return self.returncode


def test_codex_provider_kills_process_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = HangingCodexProcess()

    def fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    provider = CodexSparkProvider(timeout_seconds=0.01)

    with pytest.raises(RuntimeError, match="Codex 翻译超时"):
        asyncio.run(
            provider.translate(
                [TranslationItem(id="seg-1", text="hello")],
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
            )
        )

    assert process.killed
    assert process.waited


def test_codex_provider_kills_process_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> HangingCodexProcess:
        process = HangingCodexProcess()

        def fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
            return process

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        provider = CodexSparkProvider()
        task = asyncio.create_task(
            provider.translate(
                [TranslationItem(id="seg-1", text="hello")],
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
            )
        )
        await asyncio.to_thread(process.started.wait)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return process

    process = asyncio.run(scenario())

    assert process.killed
    assert process.waited


def test_codex_provider_never_surfaces_process_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "secret-from-codex-output"

    class FailedProcess:
        returncode: int | None = None

        def communicate(self, data: bytes) -> tuple[bytes, bytes]:
            assert data
            self.returncode = 7
            return secret.encode(), secret.encode()

        def poll(self) -> int | None:
            return self.returncode

        def wait(self) -> int:
            return 7

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return FailedProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    provider = CodexSparkProvider()

    with pytest.raises(RuntimeError, match="退出码 7") as error:
        asyncio.run(
            provider.translate(
                [TranslationItem(id="seg-1", text="hello")],
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
            )
        )

    assert secret not in str(error.value)


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
        provider.translate(
            [TranslationItem(id="1", text="good morning")],
            source_language="en",
            target_language=TargetLanguage.KO,
        )
    )
    assert output[0].translated_text == "早上好"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert '"target_language":"ko"' in calls[0]["messages"][1]["content"]


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
            provider.translate(
                [TranslationItem(id="1", text="x")],
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
            )
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
