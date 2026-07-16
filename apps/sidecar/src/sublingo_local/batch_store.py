from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from .job_store import atomic_write_json
from .models import BatchRecord

_SAFE_BATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


class BatchStore:
    """Persist batch grouping and copied configuration templates."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _batch_dir(self, batch_id: str) -> Path:
        if not _SAFE_BATCH_ID.fullmatch(batch_id):
            raise ValueError("批次 ID 无效")
        path = (self.root / batch_id).resolve()
        if path.parent != self.root:
            raise ValueError("批次目录越界")
        return path

    def batch_file(self, batch_id: str) -> Path:
        return self._batch_dir(batch_id) / "batch.json"

    def save(self, batch: BatchRecord) -> None:
        payload = batch.model_dump(mode="json")
        with self._lock:
            atomic_write_json(self.batch_file(batch.id), payload)

    def load(self) -> list[BatchRecord]:
        batches: list[BatchRecord] = []
        with self._lock:
            for path in sorted(self.root.glob("*/batch.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    batches.append(BatchRecord.model_validate(payload))
                except (OSError, TypeError, ValueError):
                    continue
        return batches

    def delete(self, batch_id: str) -> None:
        path = self._batch_dir(batch_id)
        if not path.exists():
            return
        with self._lock:
            shutil.rmtree(path)
