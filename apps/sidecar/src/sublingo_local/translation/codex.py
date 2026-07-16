from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

from ..models import ModelUsageSummary, TargetLanguage, TranslatedItem, TranslationItem
from .base import ModelUsageCallback, TranslationProvider, emit_model_usage
from .common import TRANSLATION_SCHEMA, build_translation_prompt, parse_translation_json


class CodexSparkProvider(TranslationProvider):
    """Use the local Codex CLI and its existing ChatGPT sign-in."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.3-codex-spark",
        executable: str = "codex",
        timeout_seconds: float = 300,
    ) -> None:
        self.model = model
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    async def translate(
        self,
        items: Sequence[TranslationItem],
        *,
        source_language: str,
        target_language: TargetLanguage,
        on_usage: ModelUsageCallback | None = None,
    ) -> list[TranslatedItem]:
        prompt = build_translation_prompt(items, source_language, target_language)
        raw = await self._invoke(prompt, on_usage=on_usage)
        return parse_translation_json(raw)

    async def _invoke(
        self,
        prompt: str,
        *,
        on_usage: ModelUsageCallback | None,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="captionnest-codex-") as temp_dir:
            temp = Path(temp_dir)
            schema_path = temp / "translation.schema.json"
            output_path = temp / "translation.json"
            schema_path.write_text(
                json.dumps(TRANSLATION_SCHEMA, ensure_ascii=False), encoding="utf-8"
            )
            args = [
                self.executable,
                "--ask-for-approval",
                "never",
                "exec",
                "--json",
                "--ignore-user-config",
                "--ignore-rules",
                "--disable",
                "shell_tool",
                "--disable",
                "computer_use",
                "--disable",
                "browser_use",
                "--disable",
                "in_app_browser",
                "--disable",
                "apps",
                "--disable",
                "image_generation",
                "--disable",
                "multi_agent",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--model",
                self.model,
                "-",
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                # Uvicorn's Windows reload loop uses SelectorEventLoop, which does not
                # implement asyncio subprocesses. Popen inside a worker thread works on
                # both Windows event-loop policies while keeping the request loop free.
                process = await asyncio.to_thread(
                    subprocess.Popen,
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=temp,
                    creationflags=creationflags,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("未找到 codex 命令，请先安装并登录 Codex CLI") from exc

            communication = asyncio.create_task(
                asyncio.to_thread(process.communicate, prompt.encode("utf-8"))
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    asyncio.shield(communication),
                    timeout=self.timeout_seconds,
                )
            except asyncio.CancelledError:
                stdout = await _kill_and_wait(process, communication)
                emit_model_usage(on_usage, self._usage_from_stdout(stdout))
                raise
            except TimeoutError as exc:
                stdout = await _kill_and_wait(process, communication)
                emit_model_usage(on_usage, self._usage_from_stdout(stdout))
                raise RuntimeError("Codex 翻译超时") from exc
            except BaseException:
                stdout = await _kill_and_wait(process, communication)
                emit_model_usage(on_usage, self._usage_from_stdout(stdout))
                raise

            emit_model_usage(on_usage, self._usage_from_stdout(stdout))
            if process.returncode != 0:
                # Do not surface stdout/stderr: local hooks or proxies could print secrets.
                raise RuntimeError(f"Codex 翻译失败（退出码 {process.returncode}）")
            if not output_path.exists():
                raise RuntimeError("Codex 没有生成结构化翻译结果")
            return output_path.read_text(encoding="utf-8")

    def _usage_from_stdout(self, stdout: bytes | None) -> ModelUsageSummary:
        usage: Mapping[object, object] | None = None
        if stdout:
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, ValueError, TypeError):
                    continue
                if (
                    isinstance(event, Mapping)
                    and event.get("type") == "turn.completed"
                    and isinstance(event.get("usage"), Mapping)
                ):
                    usage = event["usage"]

        input_tokens = _token_count(usage, "input_tokens")
        output_tokens = _token_count(usage, "output_tokens")
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        cached_input_tokens = _token_count(usage, "cached_input_tokens")
        reasoning_tokens = _token_count(
            usage,
            "reasoning_output_tokens",
            "reasoning_tokens",
        )
        reported = any(
            value is not None
            for value in (
                input_tokens,
                output_tokens,
                cached_input_tokens,
                reasoning_tokens,
            )
        )
        return ModelUsageSummary(
            provider="codex_spark",
            model=self.model,
            request_count=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            source="cli" if reported else "unavailable",
            complete=input_tokens is not None and output_tokens is not None,
        )


async def _kill_and_wait(
    process: subprocess.Popen[bytes],
    communication: asyncio.Task[tuple[bytes | None, bytes | None]],
) -> bytes | None:
    if process.poll() is None:
        with suppress(ProcessLookupError):
            process.kill()
    # Killing the process releases the worker blocked inside communicate().
    result: tuple[bytes | None, bytes | None] = (None, None)
    with suppress(Exception):
        result = await asyncio.shield(communication)
    with suppress(ProcessLookupError):
        await asyncio.to_thread(process.wait)
    return result[0]


def _token_count(
    usage: Mapping[object, object] | None,
    *keys: str,
) -> int | None:
    if usage is None:
        return None
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None
