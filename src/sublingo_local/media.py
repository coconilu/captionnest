from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".ts",
    ".mts",
    ".m2ts",
}


def ensure_supported_video(path: Path) -> Path:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("视频路径不是文件")
    if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise ValueError(f"不支持的视频格式：{path.suffix or '无扩展名'}")
    return path


def extract_audio(video_path: Path, audio_path: Path, *, ffmpeg: str = "ffmpeg") -> None:
    executable = shutil.which(ffmpeg)
    if not executable:
        raise RuntimeError("未找到 FFmpeg，请安装 FFmpeg 并加入 PATH")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
        creationflags=creationflags,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 提取音频失败（退出码 {result.returncode}）")
