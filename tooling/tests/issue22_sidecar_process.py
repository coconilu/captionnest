from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import uvicorn
from sublingo_local.app import create_app
from sublingo_local.models import JobStep, SchedulerSettings, StepArtifactView


class DeterministicProcessPipeline:
    """Small process-safe pipeline that exercises the real durable scheduler."""

    def __init__(
        self,
        marker_dir: Path,
        *,
        block_translation: bool,
        run_id: str,
    ) -> None:
        self.marker_dir = marker_dir.resolve()
        self.marker_dir.mkdir(parents=True, exist_ok=True)
        self.block_translation = block_translation
        self.run_id = run_id

    def _record_start(self, job_id: str, step: JobStep) -> None:
        marker = self.marker_dir / "starts.ndjson"
        with marker.open("a", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "run_id": self.run_id,
                    "job_id": job_id,
                    "step": step.value,
                },
                handle,
                ensure_ascii=False,
            )
            handle.write("\n")

    def _artifact(self, job_id: str, step: JobStep) -> StepArtifactView:
        artifact_dir = self.marker_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{job_id}-{step.value}.json"
        path.write_text("{}", encoding="utf-8")
        return StepArtifactView(
            id=f"{job_id}-{step.value}",
            step=step,
            path=str(path),
            fingerprint=f"fingerprint-{job_id}-{step.value}",
            config_fingerprint=f"config-{job_id}-{step.value}",
        )

    async def run_step(  # type: ignore[no-untyped-def]
        self,
        record,
        step,
        *,
        api_key=None,
    ) -> None:
        del api_key
        record.begin_step(step, f"process test {step.value}")
        self._record_start(record.id, step)
        if self.block_translation and step == JobStep.TRANSLATION:
            await asyncio.Event().wait()
        record.complete_step(
            step,
            self._artifact(record.id, step),
            f"process test {step.value} complete",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--marker-dir", type=Path, required=True)
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--block-translation", action="store_true")
    args = parser.parse_args()

    pipeline = DeterministicProcessPipeline(
        args.marker_dir,
        block_translation=args.block_translation,
        run_id=args.run_id,
    )
    args.pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.write_text(str(os.getpid()), encoding="ascii")
    app = create_app(
        data_dir=args.data_dir,
        pipeline=pipeline,  # type: ignore[arg-type]
        scheduler_settings=SchedulerSettings(
            worker_concurrency=1,
            cuda_asr_concurrency=1,
            cpu_asr_concurrency=1,
            translation_concurrency=1,
            io_concurrency=1,
        ),
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
