from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
from pathlib import Path

from .media import SUPPORTED_VIDEO_EXTENSIONS
from .models import OpenFolderResult, PickVideoResult


class SystemIntegrationUnavailable(RuntimeError):
    pass


def _pick_video_windows() -> PickVideoResult:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise SystemIntegrationUnavailable("当前 Python 未安装 Tk 文件选择器") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    patterns = " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_VIDEO_EXTENSIONS))
    try:
        selected = filedialog.askopenfilename(
            title="选择要生成中文字幕的视频",
            filetypes=[("视频文件", patterns), ("所有文件", "*.*")],
        )
    finally:
        root.destroy()
    if not selected:
        return PickVideoResult(selected=False)
    path = Path(selected).resolve(strict=True)
    return PickVideoResult(selected=True, path=str(path), name=path.name, size=path.stat().st_size)


async def pick_video() -> PickVideoResult:
    if platform.system() != "Windows":
        raise SystemIntegrationUnavailable("系统文件选择器首版仅在 Windows 桌面环境可用")
    try:
        return await asyncio.to_thread(_pick_video_windows)
    except SystemIntegrationUnavailable:
        raise
    except Exception as exc:
        raise SystemIntegrationUnavailable("无法打开系统文件选择器") from exc


def open_folder(path_value: str) -> OpenFolderResult:
    path = Path(path_value).expanduser().resolve(strict=True)
    folder = path if path.is_dir() else path.parent
    if not folder.is_dir():
        raise ValueError("目标不是有效文件夹")

    system = platform.system()
    if system == "Windows":
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif system == "Darwin":
        executable = shutil.which("open")
        if not executable:
            raise SystemIntegrationUnavailable("当前系统没有 open 命令")
        subprocess.Popen(
            [executable, str(folder)],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        executable = shutil.which("xdg-open")
        if not executable:
            raise SystemIntegrationUnavailable("当前系统没有 xdg-open，无法打开文件夹")
        subprocess.Popen(
            [executable, str(folder)],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return OpenFolderResult(opened=True, path=str(folder))

