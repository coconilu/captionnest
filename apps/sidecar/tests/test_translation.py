import asyncio
import json
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from sublingo_local.models import (
    ModelUsageSummary,
    TargetLanguage,
    TranslatedItem,
    TranslationItem,
    merge_model_usage,
)
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
        self, items, *, source_language, target_language, on_usage=None
    ):
        assert source_language == "en"
        assert target_language == TargetLanguage.ZH_CN
        return [TranslatedItem(id=item.id, translated_text=f"中:{item.text}") for item in items]


class MissingProvider(TranslationProvider):
    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language, on_usage=None
    ):
        return []


class ExtraAndReorderedProvider(TranslationProvider):
    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language, on_usage=None
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
        self, items, *, source_language, target_language, on_usage=None
    ):
        self.batch_sizes.append(len(items))
        if on_usage:
            on_usage(
                ModelUsageSummary(
                    provider="test",
                    model="split-model",
                    request_count=1,
                    input_tokens=len(items),
                    output_tokens=1,
                    total_tokens=len(items) + 1,
                    source="provider",
                    complete=True,
                )
            )
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
    usages: list[ModelUsageSummary] = []

    output = asyncio.run(
        TranslationService(provider).translate(
            items,
            source_language="en",
            target_language=TargetLanguage.ZH_CN,
            on_progress=lambda done, total: progress.append((done, total)),
            on_recovery=recoveries.append,
            on_usage=usages.append,
        )
    )

    assert [item.id for item in output] == ["seg-0", "seg-1", "seg-2", "seg-3"]
    assert provider.batch_sizes == [4, 2, 1, 1, 2, 1, 1]
    assert progress == [(1, 1)]
    assert len(recoveries) == 3
    assert all("拆分重试" in message for message in recoveries)
    summary = merge_model_usage(usages)
    assert summary is not None
    assert summary.request_count == 7
    assert summary.input_tokens == 12
    assert summary.output_tokens == 7
    assert summary.total_tokens == 19


def test_codex_provider_uses_parameter_array_and_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    usages: list[ModelUsageSummary] = []

    class FakeProcess:
        def __init__(self, args: list[str]) -> None:
            self.args = args
            self.returncode: int | None = None

        def communicate(self, data: bytes) -> tuple[bytes, None]:
            captured["input"] = data
            output = Path(self.args[self.args.index("--output-last-message") + 1])
            output.write_text(
                json.dumps({"items": [{"id": "seg-1", "translated_text": "你好"}]}),
                encoding="utf-8",
            )
            self.returncode = 0
            events = (
                b'{"type":"turn.started"}\n'
                b'{"type":"turn.completed","usage":{"input_tokens":21,'
                b'"cached_input_tokens":8,"output_tokens":4,'
                b'"reasoning_output_tokens":1}}\n'
            )
            return events, None

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
            on_usage=usages.append,
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
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert "--json" in args
    prompt = captured["input"].decode("utf-8")
    assert "hello" in prompt
    assert '"target_language":"zh-CN"' in prompt
    assert result == [TranslatedItem(id="seg-1", translated_text="你好")]
    assert usages == [
        ModelUsageSummary(
            provider="codex_spark",
            model="gpt-5.3-codex-spark",
            request_count=1,
            input_tokens=21,
            output_tokens=4,
            total_tokens=25,
            cached_input_tokens=8,
            reasoning_tokens=1,
            source="cli",
            complete=True,
        )
    ]


def test_codex_provider_marks_missing_structured_usage_as_unavailable() -> None:
    usage = CodexSparkProvider()._usage_from_stdout(
        b'{"type":"turn.completed"}\n'
    )

    assert usage.source == "unavailable"
    assert usage.complete is False
    assert usage.request_count == 1
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None


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
    usages: list[ModelUsageSummary] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-value"
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return httpx.Response(
            200,
            json={
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 5,
                    "total_tokens": 16,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
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
        provider_name="lmstudio",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(
        provider.translate(
            [TranslationItem(id="1", text="good morning")],
            source_language="en",
            target_language=TargetLanguage.KO,
            on_usage=usages.append,
        )
    )
    assert output[0].translated_text == "早上好"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert '"target_language":"ko"' in calls[0]["messages"][1]["content"]
    summary = merge_model_usage(usages)
    assert summary is not None
    assert summary.provider == "lmstudio"
    assert summary.request_count == 2
    assert summary.input_tokens == 11
    assert summary.output_tokens == 5
    assert summary.total_tokens == 16
    assert summary.cached_input_tokens == 3
    assert summary.reasoning_tokens == 2
    assert summary.source == "mixed"
    assert summary.complete is False


def test_usage_callback_failure_does_not_break_translation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": "1", "translated_text": "好"}]},
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
            },
        )

    provider = OpenAICompatibleProvider(
        endpoint="https://example.test/v1",
        model="model",
        transport=httpx.MockTransport(handler),
    )

    def failed_collector(usage: ModelUsageSummary) -> None:
        raise OSError("metrics store unavailable")

    output = asyncio.run(
        provider.translate(
            [TranslationItem(id="1", text="good")],
            source_language="en",
            target_language=TargetLanguage.ZH_CN,
            on_usage=failed_collector,
        )
    )
    assert output[0].translated_text == "好"


@pytest.mark.parametrize(
    ("fallback_before_cancel", "expected_requests"),
    [(False, 1), (True, 2)],
)
def test_openai_compatible_counts_cancelled_in_flight_requests(
    fallback_before_cancel: bool,
    expected_requests: int,
) -> None:
    async def scenario() -> tuple[int, list[ModelUsageSummary]]:
        entered = asyncio.Event()

        class CancellingTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.calls = 0

            async def handle_async_request(
                self,
                request: httpx.Request,
            ) -> httpx.Response:
                self.calls += 1
                if fallback_before_cancel and self.calls == 1:
                    return httpx.Response(
                        400,
                        json={"error": "unsupported response_format"},
                        request=request,
                    )
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("cancelled request must not resume")

        transport = CancellingTransport()
        usages: list[ModelUsageSummary] = []
        provider = OpenAICompatibleProvider(
            endpoint="https://example.test/v1",
            model="model",
            transport=transport,
        )
        task = asyncio.create_task(
            provider.translate(
                [TranslationItem(id="1", text="good")],
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
                on_usage=usages.append,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return transport.calls, usages

    calls, usages = asyncio.run(scenario())
    summary = merge_model_usage(usages)

    assert calls == expected_requests
    assert summary is not None
    assert summary.request_count == expected_requests
    assert summary.source == "unavailable"
    assert summary.complete is False
    assert summary.total_tokens is None


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
