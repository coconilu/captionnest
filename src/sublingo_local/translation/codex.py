from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from ..models import TargetLanguage, TranslatedItem, TranslationItem
from .base import TranslationProvider
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
    ) -> list[TranslatedItem]:
        prompt = build_translation_prompt(items, source_language, target_language)
        raw = await self._invoke(prompt)
        return parse_translation_json(raw)

    async def _invoke(self, prompt: str) -> str:
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
                    stdout=subprocess.DEVNULL,
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
                await asyncio.wait_for(
                    asyncio.shield(communication),
                    timeout=self.timeout_seconds,
                )
            except asyncio.CancelledError:
                await _kill_and_wait(process, communication)
                raise
            except TimeoutError as exc:
                await _kill_and_wait(process, communication)
                raise RuntimeError("Codex 翻译超时") from exc
            except BaseException:
                await _kill_and_wait(process, communication)
                raise

            if process.returncode != 0:
                # Do not surface stdout/stderr: local hooks or proxies could print secrets.
                raise RuntimeError(f"Codex 翻译失败（退出码 {process.returncode}）")
            if not output_path.exists():
                raise RuntimeError("Codex 没有生成结构化翻译结果")
            return output_path.read_text(encoding="utf-8")


async def _kill_and_wait(
    process: subprocess.Popen[bytes],
    communication: asyncio.Task[tuple[bytes | None, bytes | None]],
) -> None:
    if process.poll() is None:
        with suppress(ProcessLookupError):
            process.kill()
    # Killing the process releases the worker blocked inside communicate().
    with suppress(Exception):
        await asyncio.shield(communication)
    with suppress(ProcessLookupError):
        await asyncio.to_thread(process.wait)
