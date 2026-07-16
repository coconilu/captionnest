from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write(path, _json_bytes(payload))


class JobStore:
    """Persist task metadata and reusable step artifacts without runtime secrets."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _job_dir(self, job_id: str) -> Path:
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ValueError("任务 ID 无效")
        path = (self.root / job_id).resolve()
        if path.parent != self.root:
            raise ValueError("任务目录越界")
        return path

    def job_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def artifact_path(self, job_id: str, filename: str) -> Path:
        if Path(filename).name != filename or not filename:
            raise ValueError("产物文件名无效")
        artifact_root = (self._job_dir(job_id) / "artifacts").resolve()
        path = (artifact_root / filename).resolve()
        if path.parent != artifact_root:
            raise ValueError("产物路径越界")
        return path

    def save_job(self, job_id: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            atomic_write_json(self.job_file(job_id), payload)

    def load_job(self, job_id: str) -> dict[str, Any]:
        """Read one durable record for commit acknowledgement and recovery."""

        with self._lock:
            payload = json.loads(self.job_file(job_id).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("任务记录格式无效")
        return payload

    def load_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted(self.root.glob("*/job.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if isinstance(payload, dict):
                    jobs.append(payload)
        return jobs

    def write_artifact(
        self, job_id: str, filename: str, payload: Mapping[str, Any]
    ) -> tuple[Path, str]:
        content = _json_bytes(payload)
        path = self.artifact_path(job_id, filename)
        with self._lock:
            _atomic_write(path, content)
        return path, hashlib.sha256(content).hexdigest()

    def read_artifact(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve(strict=True)
        if self.root not in resolved.parents:
            raise ValueError("产物路径越界")
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("产物格式无效")
        return payload

    def delete_job(self, job_id: str) -> None:
        path = self._job_dir(job_id)
        if not path.exists():
            return
        with self._lock:
            shutil.rmtree(path)
