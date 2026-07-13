from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile

from .media import SUPPORTED_VIDEO_EXTENSIONS
from .models import UploadView

_SAFE_NAME_RE = re.compile(r"[^\w.()\-\u4e00-\u9fff\u3040-\u30ff]+", re.UNICODE)


def safe_filename(filename: str | None) -> str:
    name = Path(filename or "video.mp4").name.strip()
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    return name[:180] or "video.mp4"


class UploadStore:
    def __init__(self, root: Path, *, max_bytes: int = 50 * 1024**3) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self._items: dict[str, UploadView] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    async def save(self, upload: UploadFile) -> UploadView:
        name = safe_filename(upload.filename)
        if Path(name).suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError(f"不支持的视频格式：{Path(name).suffix or '无扩展名'}")
        upload_id = uuid.uuid4().hex
        folder = self.root / upload_id
        folder.mkdir(parents=True, exist_ok=False)
        destination = folder / name
        size = 0
        try:
            with destination.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("上传文件超过大小限制")
                    target.write(chunk)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            await upload.close()
        item = UploadView(upload_id=upload_id, name=name, path=str(destination), size=size)
        self._items[upload_id] = item
        return item

    def get(self, upload_id: str) -> UploadView:
        try:
            item = self._items[upload_id]
        except KeyError as exc:
            raise KeyError("上传文件不存在或服务已重启") from exc
        path = Path(item.path).resolve(strict=True)
        if self.root.resolve() not in path.parents:
            raise RuntimeError("上传文件路径越界")
        return item

