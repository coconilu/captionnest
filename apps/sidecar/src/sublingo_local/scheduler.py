from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from .models import (
    ASRSettings,
    JobStep,
    QueueStatus,
    SchedulerSettings,
    StepStatus,
    TranslationProviderName,
)


@dataclass(frozen=True)
class ScheduleEntry:
    job_id: str
    start_step: JobStep
    continue_pipeline: bool
    api_key: str | None = None


class JobScheduler:
    """Persistent FIFO dispatcher with bounded running tasks and step resources."""

    def __init__(
        self,
        pipeline: Any,
        record_resolver: Callable[[str], Any],
        *,
        step_order: Sequence[JobStep],
        settings: SchedulerSettings | None = None,
    ) -> None:
        self.pipeline = pipeline
        self._record_resolver = record_resolver
        self._step_order = tuple(step_order)
        self.settings = settings or SchedulerSettings()
        self._pending: deque[ScheduleEntry] = deque()
        self._pending_ids: set[str] = set()
        self._running: dict[str, asyncio.Task[None]] = {}
        self._completion: dict[str, asyncio.Future[None]] = {}
        self._user_cancelled: set[str] = set()
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._stopping = False
        self._io_slots: asyncio.Semaphore | None = None
        self._cuda_asr_slots: asyncio.Semaphore | None = None
        self._cpu_asr_slots: asyncio.Semaphore | None = None
        self._translation_slots: dict[TranslationProviderName, asyncio.Semaphore] = {}

    @property
    def completions(self) -> dict[str, asyncio.Future[None]]:
        return self._completion

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._pending_ids or job_id in self._running

    def restore(
        self,
        job_id: str,
        start_step: JobStep,
        *,
        continue_pipeline: bool,
    ) -> None:
        with self._lock:
            if job_id in self._pending_ids or job_id in self._running:
                return
            self._pending.append(
                ScheduleEntry(
                    job_id=job_id,
                    start_step=start_step,
                    continue_pipeline=continue_pipeline,
                )
            )
            self._pending_ids.add(job_id)

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._ensure_started(loop)
        with self._lock:
            for entry in self._pending:
                self._ensure_completion(entry.job_id, loop)
            self._reindex_pending_locked()
        assert self._wake is not None
        self._wake.set()

    def enqueue(
        self,
        record: Any,
        start_step: JobStep,
        *,
        api_key: str | None,
        continue_pipeline: bool,
    ) -> None:
        loop = asyncio.get_running_loop()
        self._ensure_started(loop)
        with self._lock:
            if record.id in self._pending_ids or record.id in self._running:
                raise ValueError("任务正在运行")
            self._completion.pop(record.id, None)
            self._ensure_completion(record.id, loop)
            position = len(self._pending) + 1
            record.mark_queued(
                start_step,
                continue_pipeline=continue_pipeline,
                queue_position=position,
            )
            self._pending.append(
                ScheduleEntry(
                    job_id=record.id,
                    start_step=start_step,
                    continue_pipeline=continue_pipeline,
                    api_key=api_key,
                )
            )
            self._pending_ids.add(record.id)
        assert self._wake is not None
        self._wake.set()

    def cancel(self, job_id: str) -> bool:
        wake = self._wake
        with self._lock:
            if job_id in self._pending_ids:
                self._pending = deque(
                    entry for entry in self._pending if entry.job_id != job_id
                )
                self._pending_ids.remove(job_id)
                record = self._record_resolver(job_id)
                record.cancel_current()
                self._finish_completion_locked(job_id)
                self._reindex_pending_locked()
                if wake is not None:
                    wake.set()
                return True
            task = self._running.get(job_id)
            if task is None:
                return False
            self._user_cancelled.add(job_id)
            task.cancel()
            return True

    async def wait(self, job_id: str) -> None:
        with self._lock:
            completion = self._completion.get(job_id)
        if completion is not None:
            await asyncio.shield(completion)

    async def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            dispatcher = self._dispatcher
            running = list(self._running.values())
        if dispatcher is not None:
            dispatcher.cancel()
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        if dispatcher is not None:
            await asyncio.gather(dispatcher, return_exceptions=True)
        with self._lock:
            for completion in self._completion.values():
                if not completion.done():
                    completion.cancel()

    def _ensure_started(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("调度器不能跨事件循环复用")
            if self._dispatcher is not None and not self._dispatcher.done():
                return
            self._loop = loop
            self._stopping = False
            self._wake = asyncio.Event()
            self._io_slots = asyncio.Semaphore(self.settings.io_concurrency)
            self._cuda_asr_slots = asyncio.Semaphore(
                self.settings.cuda_asr_concurrency
            )
            self._cpu_asr_slots = asyncio.Semaphore(self.settings.cpu_asr_concurrency)
            self._translation_slots = {
                provider: asyncio.Semaphore(self.settings.translation_concurrency)
                for provider in TranslationProviderName
            }
            self._dispatcher = asyncio.create_task(
                self._dispatch(),
                name="captionnest-scheduler",
            )

    def _ensure_completion(
        self,
        job_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.Future[None]:
        completion = self._completion.get(job_id)
        if completion is None or completion.done():
            completion = loop.create_future()
            self._completion[job_id] = completion
        return completion

    async def _dispatch(self) -> None:
        assert self._wake is not None
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while True:
                    with self._lock:
                        if self._stopping:
                            return
                        if (
                            not self._pending
                            or len(self._running) >= self.settings.worker_concurrency
                        ):
                            break
                        entry = self._pending.popleft()
                        self._pending_ids.remove(entry.job_id)
                        record = self._record_resolver(entry.job_id)
                        if record.queue_status != QueueStatus.QUEUED:
                            self._finish_completion_locked(entry.job_id)
                            continue
                        record.mark_scheduler_running()
                        task = asyncio.create_task(
                            self._execute(entry),
                            name=f"captionnest-job-{entry.job_id}",
                        )
                        self._running[entry.job_id] = task
                        task.add_done_callback(
                            lambda completed, job_id=entry.job_id: self._task_done(
                                job_id,
                                completed,
                            )
                        )
                        self._reindex_pending_locked()
        except asyncio.CancelledError:
            return

    async def _execute(self, entry: ScheduleEntry) -> None:
        record = self._record_resolver(entry.job_id)
        try:
            run_step = getattr(self.pipeline, "run_step", None)
            if callable(run_step):
                start_index = self._step_order.index(entry.start_step)
                selected = (
                    self._step_order[start_index:]
                    if entry.continue_pipeline
                    else (entry.start_step,)
                )
                for step in selected:
                    async with self._resource_slot(record, step):
                        await run_step(record, step, api_key=entry.api_key)
                if all(
                    record.steps[step].status == StepStatus.SUCCEEDED
                    for step in self._step_order
                ):
                    record.mark_complete()
                else:
                    record.mark_paused()
            else:
                await self.pipeline.run_from(
                    record,
                    entry.start_step,
                    api_key=entry.api_key,
                    continue_pipeline=entry.continue_pipeline,
                )
        except asyncio.CancelledError:
            with self._lock:
                cancelled_by_user = entry.job_id in self._user_cancelled
            if cancelled_by_user:
                record.cancel_current()
            else:
                record.mark_interrupted()
        except Exception as exc:
            record.fail_current(exc, secrets=(entry.api_key or "",))

    def _resource_slot(
        self,
        record: Any,
        step: JobStep,
    ) -> AbstractAsyncContextManager[Any]:
        if step in {JobStep.MEDIA, JobStep.EXPORT}:
            assert self._io_slots is not None
            return self._io_slots
        if step == JobStep.TRANSCRIPTION:
            asr = record.asr
            if isinstance(asr, ASRSettings) and asr.device == "cpu":
                assert self._cpu_asr_slots is not None
                return self._cpu_asr_slots
            assert self._cuda_asr_slots is not None
            return self._cuda_asr_slots
        assert self._translation_slots
        return self._translation_slots[record.translation.provider]

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        del task
        with self._lock:
            self._running.pop(job_id, None)
            self._user_cancelled.discard(job_id)
            self._finish_completion_locked(job_id)
            wake = self._wake
        if wake is not None:
            wake.set()

    def _finish_completion_locked(self, job_id: str) -> None:
        completion = self._completion.get(job_id)
        if completion is not None and not completion.done():
            completion.set_result(None)

    def _reindex_pending_locked(self) -> None:
        for position, entry in enumerate(self._pending, start=1):
            record = self._record_resolver(entry.job_id)
            record.set_queue_position(position)
