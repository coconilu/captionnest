from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sublingo_local.batch_store import BatchStore
from sublingo_local.job_store import JobStore
from sublingo_local.jobs import JobManager, JobRecord
from sublingo_local.models import (
    BatchConfigSnapshot,
    BatchRecord,
    BatchStatusSummary,
    JobCreateRequest,
    JobRunRequest,
    JobStep,
    MediaStepSettings,
    SchedulerSettings,
    StepArtifactView,
    StepStatus,
    TranslationStepSettings,
)


def _artifact(step: JobStep, suffix: str = "fixture") -> StepArtifactView:
    return StepArtifactView(
        id=f"{step.value}-{suffix}",
        step=step,
        path=f"{step.value}-{suffix}.json",
        fingerprint=f"fingerprint-{step.value}-{suffix}",
        config_fingerprint=f"config-{step.value}-{suffix}",
    )


def _mark_succeeded(record: JobRecord, *steps: JobStep) -> None:
    for step in steps:
        state = record.steps[step]
        state.status = StepStatus.SUCCEEDED
        state.progress = 100
        state.artifact = _artifact(step)
    record.update()


async def _eventually(predicate, *, attempts: int = 200) -> None:  # type: ignore[no-untyped-def]
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached")


class GateStepPipeline:
    def __init__(self, *, block_all: bool = True) -> None:
        self.block_all = block_all
        self.block_job_id: str | None = None
        self.release = asyncio.Event()
        self.started: list[str] = []
        self.active = 0
        self.maximum_active = 0

    async def run_step(  # type: ignore[no-untyped-def]
        self,
        record,
        step,
        *,
        api_key=None,
    ) -> None:
        del api_key
        record.begin_step(step, f"test {step.value}")
        self.started.append(record.id)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.block_all or record.id == self.block_job_id:
                await self.release.wait()
        finally:
            self.active -= 1
        record.complete_step(step, _artifact(step, record.id), "test complete")


def _manager(
    tmp_path: Path,
    pipeline: object,
    *,
    worker_concurrency: int,
) -> tuple[JobStore, JobManager]:
    store = JobStore(tmp_path / "jobs")
    manager = JobManager(
        None,
        pipeline,  # type: ignore[arg-type]
        job_store=store,
        scheduler_settings=SchedulerSettings(
            worker_concurrency=worker_concurrency,
            cuda_asr_concurrency=1,
            cpu_asr_concurrency=2,
            translation_concurrency=2,
            io_concurrency=2,
        ),
    )
    return store, manager


@pytest.mark.asyncio
async def test_scheduler_limits_cuda_and_preserves_fifo_without_duplicate_claims(
    tmp_path: Path,
) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    pipeline = GateStepPipeline()
    _, manager = _manager(tmp_path, pipeline, worker_concurrency=3)
    job_ids: list[str] = []

    for _ in range(3):
        created = manager.create(JobCreateRequest(video_path=str(video)))
        record = manager._record(created.id)
        _mark_succeeded(record, JobStep.MEDIA)
        manager.run_step(
            created.id,
            JobStep.TRANSCRIPTION,
            JobRunRequest(continue_pipeline=False),
        )
        job_ids.append(created.id)

    await _eventually(lambda: len(pipeline.started) == 1)
    assert pipeline.started == [job_ids[0]]
    assert pipeline.maximum_active == 1
    assert manager.scheduler.running_count == 3
    assert manager.scheduler.pending_count == 0
    with pytest.raises(ValueError, match="任务正在运行"):
        manager.run(job_ids[0])

    pipeline.release.set()
    for job_id in job_ids:
        await manager.wait(job_id)

    assert pipeline.started == job_ids
    assert pipeline.maximum_active == 1
    assert all(
        manager.get(job_id).steps[1].status == StepStatus.SUCCEEDED
        for job_id in job_ids
    )
    await manager.shutdown()


@pytest.mark.asyncio
async def test_scheduler_cancels_queued_and_running_jobs_independently(
    tmp_path: Path,
) -> None:
    video = tmp_path / "cancel.mp4"
    video.write_bytes(b"video")
    pipeline = GateStepPipeline()
    _, manager = _manager(tmp_path, pipeline, worker_concurrency=1)
    first = manager.create(JobCreateRequest(video_path=str(video)))
    second = manager.create(JobCreateRequest(video_path=str(video)))

    manager.run_step(
        first.id,
        JobStep.MEDIA,
        JobRunRequest(continue_pipeline=False),
    )
    manager.run_step(
        second.id,
        JobStep.MEDIA,
        JobRunRequest(continue_pipeline=False),
    )
    await _eventually(lambda: pipeline.started == [first.id])
    assert manager.get(second.id).queue_position == 1

    queued = manager.cancel(second.id)
    assert queued.status == "cancelled"
    assert manager.scheduler.pending_count == 0
    running = manager.cancel(first.id)
    assert running.status in {"queued", "running"}
    await manager.wait(first.id)
    await manager.wait(second.id)

    first_view = manager.get(first.id)
    assert first_view.status == "cancelled"
    assert first_view.steps[0].status == "cancelled"
    assert manager.get(second.id).status == "cancelled"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_restart_recovers_fifo_and_requires_deepseek_runtime_key(
    tmp_path: Path,
) -> None:
    video = tmp_path / "restart.mp4"
    video.write_bytes(b"video")
    blocking = GateStepPipeline(block_all=False)
    store, manager = _manager(tmp_path, blocking, worker_concurrency=1)
    first = manager.create(JobCreateRequest(video_path=str(video)))
    deepseek = manager.create(
        JobCreateRequest(
            video_path=str(video),
            translation={"provider": "deepseek", "model": "deepseek-v4-flash"},
        )
    )
    second = manager.create(JobCreateRequest(video_path=str(video)))
    third = manager.create(JobCreateRequest(video_path=str(video)))
    blocking.block_job_id = first.id

    manager.run_step(
        first.id,
        JobStep.MEDIA,
        JobRunRequest(continue_pipeline=False),
    )
    await _eventually(lambda: blocking.started == [first.id])
    deepseek_record = manager._record(deepseek.id)
    _mark_succeeded(deepseek_record, JobStep.MEDIA, JobStep.TRANSCRIPTION)
    manager.run_step(
        deepseek.id,
        JobStep.TRANSLATION,
        JobRunRequest(api_key="runtime-only-secret", continue_pipeline=False),
    )
    for created in (second, third):
        manager.run_step(
            created.id,
            JobStep.MEDIA,
            JobRunRequest(continue_pipeline=False),
        )

    assert [
        manager.get(job_id).queue_position
        for job_id in (deepseek.id, second.id, third.id)
    ] == [1, 2, 3]
    assert "runtime-only-secret" not in store.job_file(deepseek.id).read_text(
        encoding="utf-8"
    )
    await manager.shutdown()

    assert manager.get(first.id).status == "interrupted"
    assert manager.get(first.id).steps[0].status == "interrupted"
    completing = GateStepPipeline(block_all=False)
    reloaded = JobManager(
        None,
        completing,  # type: ignore[arg-type]
        job_store=store,
        scheduler_settings=SchedulerSettings(worker_concurrency=1),
    )
    waiting = reloaded.get(deepseek.id)
    assert waiting.status == "waiting_for_input"
    assert waiting.queue_status == "waiting_for_input"
    assert waiting.steps[0].artifact is not None
    assert waiting.steps[1].artifact is not None
    assert "runtime-only-secret" not in store.job_file(deepseek.id).read_text(
        encoding="utf-8"
    )

    reloaded.start()
    await reloaded.wait(second.id)
    await reloaded.wait(third.id)
    assert completing.started == [second.id, third.id]
    assert reloaded.get(second.id).queue_position is None
    assert reloaded.get(third.id).queue_position is None

    with pytest.raises(ValueError, match="DeepSeek 需要 API Key"):
        reloaded.run_step(
            deepseek.id,
            JobStep.TRANSLATION,
            JobRunRequest(continue_pipeline=False),
        )
    assert reloaded.get(deepseek.id).status == "waiting_for_input"
    reloaded.run_step(
        deepseek.id,
        JobStep.TRANSLATION,
        JobRunRequest(api_key="replacement-secret", continue_pipeline=False),
    )
    await reloaded.wait(deepseek.id)
    assert reloaded.get(deepseek.id).steps[2].status == "succeeded"
    assert "replacement-secret" not in store.job_file(deepseek.id).read_text(
        encoding="utf-8"
    )
    await reloaded.shutdown()


def test_batch_store_round_trip_and_job_payload_legacy_defaults(tmp_path: Path) -> None:
    batch_store = BatchStore(tmp_path / "batches")
    batch = BatchRecord(
        id="batch-001",
        name="十个视频",
        job_ids=["job-a", "job-b"],
        config_template=BatchConfigSnapshot(),
        status_summary=BatchStatusSummary(total=2, draft=2),
    )

    batch_store.save(batch)
    assert batch_store.load() == [batch]
    assert "api_key" not in batch_store.batch_file(batch.id).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="批次 ID 无效"):
        batch_store.batch_file("../escape")

    record = JobRecord(
        id="legacy-job",
        media=MediaStepSettings(
            source_kind="path",
            path="legacy.mp4",
            name="legacy.mp4",
        ),
    )
    payload = record.to_payload()
    for field in (
        "batch_id",
        "queue_status",
        "queue_position",
        "priority",
        "interrupted_at",
        "queued_start_step",
        "queued_continue_pipeline",
    ):
        payload.pop(field, None)
    payload["schema_version"] = 2

    loaded = JobRecord.from_payload(payload)
    assert loaded.batch_id is None
    assert loaded.queue_status == "draft"
    assert loaded.queue_position is None
    assert loaded.priority == 0
    assert loaded.interrupted_at is None
    summary = loaded.to_summary().model_dump(mode="json")
    assert summary["current_step"] == "media"
    assert "logs" not in summary
    assert "steps" not in summary
    assert "attempts" not in summary


def test_running_deepseek_reload_keeps_interrupted_attempt_and_waits_for_key(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs")
    record = JobRecord(
        id="running-deepseek",
        media=MediaStepSettings(
            source_kind="path",
            path="video.mp4",
            name="video.mp4",
        ),
        translation=TranslationStepSettings(
            provider="deepseek",
            model="deepseek-v4-flash",
        ),
    )
    _mark_succeeded(record, JobStep.MEDIA, JobStep.TRANSCRIPTION)
    record.mark_queued(
        JobStep.TRANSLATION,
        continue_pipeline=False,
        queue_position=1,
    )
    record.begin_step(JobStep.TRANSLATION, "running before restart")
    store.save_job(record.id, record.to_payload())

    reloaded = JobManager(
        None,
        GateStepPipeline(block_all=False),  # type: ignore[arg-type]
        job_store=store,
    )
    view = reloaded.get(record.id)

    assert view.status == "waiting_for_input"
    assert view.queue_status == "waiting_for_input"
    assert view.interrupted_at is not None
    assert view.steps[2].status == "interrupted"
    assert view.steps[2].attempts[-1].status == "interrupted"
    assert view.steps[0].artifact is not None
    assert view.steps[1].artifact is not None
