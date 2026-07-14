from __future__ import annotations

import ctypes
import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from importlib import metadata
from typing import Literal

from pydantic import BaseModel

from .model_manager import ModelManager

ComponentStatus = Literal["ready", "missing", "broken", "failed"]


class RuntimeStatus(BaseModel):
    status: Literal["ready", "failed"]
    version: str | None = None
    message: str | None = None


class ComponentView(BaseModel):
    status: ComponentStatus
    provider: str | None = None
    version: str | None = None
    message: str | None = None


class ModelStatusView(BaseModel):
    status: Literal["ready", "missing", "downloading", "damaged"]
    name: str | None = None
    path: str | None = None
    message: str | None = None


class AccelerationStatus(BaseModel):
    status: Literal["cpu", "cuda_ready", "cuda_unavailable"]
    device: Literal["cpu", "cuda"]
    cuda_available: bool
    message: str | None = None


class CodexStatus(BaseModel):
    status: Literal["not_installed", "not_logged_in", "ready", "check_failed"]
    version: str | None = None
    install_url: str | None = None
    message: str | None = None


class ToolStatus(BaseModel):
    media: ComponentView


class EnvironmentView(BaseModel):
    runtime: RuntimeStatus
    asr: ComponentView
    model: ModelStatusView
    acceleration: AccelerationStatus
    codex: CodexStatus
    tools: ToolStatus


class EnvironmentService:
    """Run lightweight, repeatable checks without importing ASR at module import time."""

    CODEX_INSTALL_URL = "https://developers.openai.com/codex/cli/"

    def __init__(self, models: ModelManager, *, default_model: str = "small") -> None:
        self.models = models
        self.default_model = default_model

    def check(self) -> EnvironmentView:
        return EnvironmentView(
            runtime=RuntimeStatus(
                status="ready",
                version=".".join(str(part) for part in sys.version_info[:3]),
            ),
            asr=self._check_asr(),
            model=self._check_model(),
            acceleration=self._check_acceleration(),
            codex=self._check_codex(),
            tools=ToolStatus(media=self._check_media()),
        )

    def _check_asr(self) -> ComponentView:
        if importlib.util.find_spec("faster_whisper") is None:
            return ComponentView(
                status="missing",
                provider="Faster-Whisper",
                message="语音识别组件尚未安装",
            )
        try:
            # This is deliberately delayed until the user/environment check runs.
            importlib.import_module("faster_whisper")
        except (ImportError, OSError, RuntimeError):
            return ComponentView(
                status="broken",
                provider="Faster-Whisper",
                version=self._package_version("faster-whisper"),
                message="语音识别组件无法加载，请重新安装应用",
            )
        return ComponentView(
            status="ready",
            provider="Faster-Whisper",
            version=self._package_version("faster-whisper"),
        )

    def _check_model(self) -> ModelStatusView:
        item = self.models.get(self.default_model)
        return ModelStatusView(
            status=item.status,
            name=item.id,
            path=item.path,
            message=item.message,
        )

    def _check_acceleration(self) -> AccelerationStatus:
        try:
            ctranslate2 = importlib.import_module("ctranslate2")
            if int(ctranslate2.get_cuda_device_count()) > 0:
                missing_libraries = self._missing_cuda_libraries()
                if missing_libraries:
                    return AccelerationStatus(
                        status="cuda_unavailable",
                        device="cpu",
                        cuda_available=False,
                        message=(
                            "检测到 NVIDIA 显卡，但缺少 CUDA 12 / cuDNN 9 运行库，"
                            "将使用 CPU"
                        ),
                    )
                return AccelerationStatus(
                    status="cuda_ready",
                    device="cuda",
                    cuda_available=True,
                    message="已检测到可用的 NVIDIA CUDA 加速",
                )
        except (AttributeError, ImportError, OSError, RuntimeError):
            if shutil.which("nvidia-smi"):
                return AccelerationStatus(
                    status="cuda_unavailable",
                    device="cpu",
                    cuda_available=False,
                    message="检测到 NVIDIA 显卡，但当前加速组件不可用，将使用 CPU",
                )
        if shutil.which("nvidia-smi"):
            return AccelerationStatus(
                status="cuda_unavailable",
                device="cpu",
                cuda_available=False,
                message="检测到 NVIDIA 显卡，但 CUDA 运行环境不可用，将使用 CPU",
            )
        return AccelerationStatus(
            status="cpu",
            device="cpu",
            cuda_available=False,
            message="当前使用 CPU，无需额外安装 GPU 环境",
        )

    @staticmethod
    def _missing_cuda_libraries() -> list[str]:
        if os.name == "nt":
            loader = ctypes.WinDLL
            names = ("cublas64_12.dll", "cudnn64_9.dll")
        else:
            loader = ctypes.CDLL
            names = ("libcublas.so.12", "libcudnn.so.9")
        missing: list[str] = []
        for name in names:
            try:
                loader(name)
            except OSError:
                missing.append(name)
        return missing

    def _check_codex(self) -> CodexStatus:
        self._refresh_windows_path()
        executable = shutil.which("codex")
        if not executable:
            return CodexStatus(
                status="not_installed",
                install_url=self.CODEX_INSTALL_URL,
                message="未检测到 Codex CLI；仅使用 Codex 翻译时需要安装",
            )
        try:
            version_result = self._run([executable, "--version"])
            if version_result.returncode != 0:
                raise RuntimeError("version check failed")
            version = self._parse_codex_version(version_result.stdout)
            login_result = self._run([executable, "login", "status"])
        except (OSError, subprocess.SubprocessError, RuntimeError):
            return CodexStatus(
                status="check_failed",
                install_url=self.CODEX_INSTALL_URL,
                message="Codex 状态检测失败，请确认命令可以在终端中运行",
            )
        if login_result.returncode != 0:
            return CodexStatus(
                status="not_logged_in",
                version=version,
                install_url=self.CODEX_INSTALL_URL,
                message="Codex 已安装但尚未登录，请在终端运行 codex login",
            )
        return CodexStatus(
            status="ready",
            version=version,
            install_url=self.CODEX_INSTALL_URL,
            message="Codex 已安装并登录",
        )

    def _check_media(self) -> ComponentView:
        if importlib.util.find_spec("av") is None:
            return ComponentView(
                status="missing",
                provider="PyAV（应用内置 FFmpeg）",
                message="媒体解码组件缺失，请重新安装应用",
            )
        try:
            importlib.import_module("av")
        except (ImportError, OSError, RuntimeError):
            return ComponentView(
                status="broken",
                provider="PyAV（应用内置 FFmpeg）",
                version=self._package_version("av"),
                message="媒体解码组件无法加载，请重新安装应用",
            )
        return ComponentView(
            status="ready",
            provider="PyAV（应用内置 FFmpeg）",
            version=self._package_version("av"),
            message="无需单独安装 FFmpeg",
        )

    @staticmethod
    def _package_version(package: str) -> str | None:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return None

    @staticmethod
    def _parse_codex_version(output: str) -> str | None:
        match = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][\w.-]+)?", output)
        return match.group(0) if match else None

    @staticmethod
    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            shell=False,
            creationflags=creationflags,
        )

    @staticmethod
    def _refresh_windows_path() -> None:
        """Pick up installers that changed PATH while the desktop app stayed open."""
        if os.name != "nt":
            return
        try:
            import winreg

            registry_paths: list[str] = []
            locations = (
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
                (winreg.HKEY_CURRENT_USER, r"Environment"),
            )
            for hive, key_name in locations:
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        value, _ = winreg.QueryValueEx(key, "Path")
                    if isinstance(value, str):
                        registry_paths.extend(os.path.expandvars(value).split(os.pathsep))
                except OSError:
                    continue
        except (ImportError, OSError):
            return

        entries = os.environ.get("PATH", "").split(os.pathsep) + registry_paths
        unique: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            normalized = entry.strip().strip('"')
            key = os.path.normcase(normalized)
            if normalized and key not in seen:
                seen.add(key)
                unique.append(normalized)
        os.environ["PATH"] = os.pathsep.join(unique)
