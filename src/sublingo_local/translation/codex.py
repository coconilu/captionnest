from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..models import TranslatedItem, TranslationItem
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
        self, items: Sequence[TranslationItem], *, source_language: str
    ) -> list[TranslatedItem]:
        prompt = build_translation_prompt(items, source_language)
        raw = await asyncio.to_thread(self._invoke, prompt)
        return parse_translation_json(raw)

    def _invoke(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="sublingo-codex-") as temp_dir:
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
                result = subprocess.run(
                    args,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                    cwd=temp,
                    creationflags=creationflags,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("未找到 codex 命令，请先安装并登录 Codex CLI") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Codex 翻译超时") from exc
            if result.returncode != 0:
                # Do not surface stdout/stderr: local hooks or proxies could print secrets.
                raise RuntimeError(f"Codex 翻译失败（退出码 {result.returncode}）")
            if not output_path.exists():
                raise RuntimeError("Codex 没有生成结构化翻译结果")
            return output_path.read_text(encoding="utf-8")
