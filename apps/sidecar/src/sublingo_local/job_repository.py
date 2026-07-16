from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeVar

from .job_store import JobStore


class PersistableJob(Protocol):
    id: str

    def attach_persistence(
        self,
        callback: Callable[[Mapping[str, Any]], None] | None,
    ) -> None: ...

    def to_payload(self) -> dict[str, Any]: ...


JobRecordT = TypeVar("JobRecordT", bound=PersistableJob)


class JobRepository:
    """Thread-safe in-memory index backed by atomic per-job JSON records."""

    def __init__(
        self,
        store: JobStore | None,
        decoder: Callable[[Mapping[str, Any]], JobRecordT],
    ) -> None:
        self.store = store
        self._decoder = decoder
        self._records: dict[str, JobRecordT] = {}
        self._lock = threading.RLock()
        self._load()

    @property
    def records(self) -> dict[str, JobRecordT]:
        return self._records

    def _attach(self, record: JobRecordT) -> None:
        if self.store is None:
            record.attach_persistence(None)
            return
        record.attach_persistence(
            lambda payload, job_id=record.id: self.store.save_job(job_id, payload)
        )

    def _load(self) -> None:
        if self.store is None:
            return
        for payload in self.store.load_jobs():
            try:
                record = self._decoder(payload)
            except (KeyError, TypeError, ValueError):
                continue
            self._attach(record)
            self._records[record.id] = record

    def add(self, record: JobRecordT) -> None:
        with self._lock:
            if record.id in self._records:
                raise ValueError("任务 ID 已存在")
            self._attach(record)
            self._records[record.id] = record
            if self.store is not None:
                self.store.save_job(record.id, record.to_payload())

    def get(self, job_id: str) -> JobRecordT:
        with self._lock:
            try:
                return self._records[job_id]
            except KeyError as exc:
                raise KeyError("任务不存在") from exc

    def list(self) -> list[JobRecordT]:
        with self._lock:
            return list(self._records.values())

    def delete(self, job_id: str) -> None:
        with self._lock:
            if job_id not in self._records:
                raise KeyError("任务不存在")
            self._records.pop(job_id)
            if self.store is not None:
                self.store.delete_job(job_id)
