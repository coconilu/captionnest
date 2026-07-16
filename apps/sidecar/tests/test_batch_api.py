from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sublingo_local.app import create_app
from sublingo_local.models import JobStep, SchedulerSettings, StepArtifactView


class ImmediatePipeline:
    async def run_step(  # type: ignore[no-untyped-def]
        self,
        record,
        step,
        *,
        api_key=None,
    ) -> None:
        del api_key
        record.begin_step(step, f"test {step.value}")
        if record.source.name == "bad.mp4" and step == JobStep.MEDIA:
            raise RuntimeError("intentional batch failure")
        path = (
            record.source.path.with_suffix(".srt")
            if step == JobStep.EXPORT
            else Path(f"{step.value}.json")
        )
        record.complete_step(
            step,
            StepArtifactView(
                id=f"{step.value}-test",
                step=step,
                path=str(path),
                fingerprint=f"fingerprint-{step.value}",
                config_fingerprint=f"config-{step.value}",
            ),
            f"test {step.value} complete",
        )


class BlockingPipeline:
    def __init__(self) -> None:
        self.started = threading.Event()

    async def run_step(  # type: ignore[no-untyped-def]
        self,
        record,
        step,
        *,
        api_key=None,
    ) -> None:
        del api_key
        record.begin_step(step, f"blocking {step.value}")
        self.started.set()
        await asyncio.Future()


@pytest.fixture(autouse=True)
def clear_desktop_session_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTIONNEST_SESSION_TOKEN", raising=False)


def _video(path: Path, content: bytes = b"video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _create_batch(
    client: TestClient,
    videos: list[Path],
    **payload: object,
) -> dict[str, object]:
    response = client.post(
        "/api/batches",
        json={
            "sources": [{"video_path": str(video)} for video in videos],
            **payload,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_terminal_jobs(
    client: TestClient,
    job_ids: list[str],
) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    for _ in range(100):
        jobs = [client.get(f"/api/jobs/{job_id}").json() for job_id in job_ids]
        if all(job["status"] in {"completed", "failed"} for job in jobs):
            return jobs
        time.sleep(0.01)
    raise AssertionError("jobs did not reach terminal states")


def test_job_summary_keyset_pagination_filters_and_legacy_shape(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        job_ids = []
        for index in range(5):
            video = _video(tmp_path / f"alpha-{index}.mp4")
            created = client.post("/api/jobs", json={"video_path": str(video)})
            assert created.status_code == 200
            job_ids.append(created.json()["id"])

        legacy = client.get("/api/jobs")
        assert legacy.status_code == 200
        assert isinstance(legacy.json(), list)
        assert len(legacy.json()) == 5
        assert {"logs", "steps"} <= legacy.json()[0].keys()

        first_page = client.get(
            "/api/jobs",
            params=[("limit", "2"), ("status", "draft"), ("q", "alpha")],
        )
        assert first_page.status_code == 200
        page = first_page.json()
        assert page["total"] == 5
        assert page["has_more"] is True
        assert len(page["items"]) == 2
        assert "logs" not in page["items"][0]
        assert "steps" not in page["items"][0]
        first_ids = {item["id"] for item in page["items"]}

        second_page = client.get(
            "/api/jobs",
            params={"limit": 2, "cursor": page["next_cursor"]},
        )
        assert second_page.status_code == 200
        second_ids = {item["id"] for item in second_page.json()["items"]}
        assert not first_ids & second_ids

        invalid = client.get("/api/jobs", params={"limit": 2, "cursor": "%%%"})
        assert invalid.status_code == 400

        watermark = page["server_time"]
        unchanged = client.get(
            "/api/jobs",
            params={"limit": 20, "updated_after": watermark},
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["total"] == 0

        newest = _video(tmp_path / "newest.mp4")
        created = client.post("/api/jobs", json={"video_path": str(newest)})
        assert created.status_code == 200
        incremental = client.get(
            "/api/jobs",
            params={"limit": 20, "updated_after": watermark},
        )
        assert incremental.status_code == 200
        assert [item["id"] for item in incremental.json()["items"]] == [
            created.json()["id"]
        ]
        assert set(job_ids).isdisjoint(
            item["id"] for item in incremental.json()["items"]
        )


def test_batch_creates_ten_independent_jobs_persists_and_never_stores_key(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    videos = [_video(tmp_path / f"lesson-{index}.mp4") for index in range(10)]
    secret = "batch-runtime-secret-must-not-persist"
    payload = {
        "name": "十文件批次",
        "sources": [{"video_path": str(video)} for video in videos],
        "api_key": secret,
        "config": {
            "target_language": "zh-CN",
            "translation": {
                "target_language": "ko",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            },
            "export": {"output_directory": str(output_dir)},
        },
    }
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        preflight = client.post(
            "/api/batches/preflight",
            json={"sources": payload["sources"], "config": payload["config"]},
        )
        assert preflight.status_code == 200
        assert preflight.json()["valid_count"] == 10
        assert preflight.json()["invalid_count"] == 0

        created = client.post("/api/batches", json=payload)
        assert created.status_code == 200
        assert secret not in created.text
        result = created.json()
        assert result["created_count"] == 10
        assert result["failed_count"] == 0
        batch = result["batch"]
        assert len(batch["job_ids"]) == 10
        assert batch["status_summary"]["draft"] == 10
        assert batch["config_template"]["target_language"] == "zh-CN"
        assert batch["config_template"]["translation"]["target_language"] == "zh-CN"

        page = client.get(
            "/api/jobs",
            params={"limit": 20, "batch_id": batch["id"]},
        )
        assert page.status_code == 200
        assert page.json()["total"] == 10
        assert {item["batch_id"] for item in page.json()["items"]} == {
            batch["id"]
        }

        first_id, second_id = batch["job_ids"][:2]
        first = client.get(f"/api/jobs/{first_id}").json()
        translation = next(
            step for step in first["steps"] if step["id"] == "translation"
        )
        updated_config = dict(translation["config"])
        updated_config["target_language"] = "ko"
        updated = client.patch(
            f"/api/jobs/{first_id}/steps/translation/config",
            json={"config": updated_config},
        )
        assert updated.status_code == 200
        second = client.get(f"/api/jobs/{second_id}").json()
        second_translation = next(
            step for step in second["steps"] if step["id"] == "translation"
        )
        assert second_translation["config"]["target_language"] == "zh-CN"

    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in data_dir.rglob("*.json")
    )
    assert secret not in persisted

    reloaded = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(reloaded) as client:
        batch_view = client.get(f"/api/batches/{batch['id']}")
        assert batch_view.status_code == 200
        assert len(batch_view.json()["job_ids"]) == 10


def test_preflight_reports_per_source_duplicate_output_and_existing_errors(
    tmp_path: Path,
) -> None:
    output = tmp_path / "shared-output"
    output.mkdir()
    first = _video(tmp_path / "a" / "same.mp4")
    second = _video(tmp_path / "b" / "same.mp4")
    unsupported = _video(tmp_path / "bad.txt")
    app = create_app(data_dir=tmp_path / "data", pipeline=ImmediatePipeline())  # type: ignore[arg-type]

    with TestClient(app) as client:
        conflict = client.post(
            "/api/batches/preflight",
            json={
                "sources": [
                    {"video_path": str(first)},
                    {"video_path": str(second)},
                ],
                "config": {"export": {"output_directory": str(output)}},
            },
        )
        assert conflict.status_code == 200
        assert conflict.json()["valid_count"] == 0
        assert conflict.json()["has_output_conflicts"] is True
        assert all(
            "output_conflict" in {issue["code"] for issue in item["issues"]}
            for item in conflict.json()["items"]
        )

        resolved = client.post(
            "/api/batches/preflight",
            json={
                "sources": [
                    {"video_path": str(first)},
                    {
                        "video_path": str(second),
                        "export": {"output_directory": str(tmp_path / "other")},
                    },
                ],
                "config": {"export": {"output_directory": str(output)}},
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["valid_count"] == 2

        duplicate = client.post(
            "/api/batches/preflight",
            json={
                "sources": [
                    {"video_path": str(first)},
                    {"video_path": str(first)},
                ]
            },
        )
        assert duplicate.status_code == 200
        assert all(
            "duplicate_source" in {issue["code"] for issue in item["issues"]}
            for item in duplicate.json()["items"]
        )

        existing = output / "same.srt"
        existing.write_text("existing subtitle", encoding="utf-8")
        exists = client.post(
            "/api/batches/preflight",
            json={
                "sources": [{"video_path": str(first)}],
                "config": {
                    "export": {
                        "output_directory": str(output),
                        "overwrite_existing": False,
                    }
                },
            },
        )
        assert exists.status_code == 200
        assert exists.json()["items"][0]["issues"][0]["code"] == "output_exists"

        before_rejected = len(client.get("/api/jobs").json())
        missing_runtime_key = client.post(
            "/api/batches",
            json={
                "sources": [{"video_path": str(second)}],
                "auto_start": True,
                "api_key": "   ",
                "config": {
                    "translation": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                    }
                },
            },
        )
        assert missing_runtime_key.status_code == 200
        assert missing_runtime_key.json()["batch"] is None
        assert missing_runtime_key.json()["created_count"] == 0
        assert len(client.get("/api/jobs").json()) == before_rejected

        partial = _create_batch(client, [first, unsupported])
        assert partial["created_count"] == 1
        assert partial["failed_count"] == 1
        assert partial["batch"] is not None


def test_bulk_upload_actions_failure_isolation_and_batch_delete_modes(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    good_one = _video(tmp_path / "good-one.mp4")
    bad = _video(tmp_path / "bad.mp4")
    good_two = _video(tmp_path / "good-two.mp4")

    with TestClient(app) as client:
        uploads = client.post(
            "/api/uploads/bulk",
            files=[
                ("files", ("uploaded.mp4", b"video", "video/mp4")),
                ("files", ("rejected.txt", b"text", "text/plain")),
            ],
        )
        assert uploads.status_code == 200
        assert uploads.json()["succeeded"] == 1
        assert uploads.json()["failed"] == 1
        uploaded = next(
            result["upload"]
            for result in uploads.json()["results"]
            if result["ok"]
        )
        uploaded_batch = client.post(
            "/api/batches",
            json={"sources": [{"upload_id": uploaded["upload_id"]}]},
        )
        assert uploaded_batch.status_code == 200
        assert uploaded_batch.json()["created_count"] == 1

        created = _create_batch(client, [good_one, bad, good_two])
        batch = created["batch"]
        assert isinstance(batch, dict)
        job_ids = batch["job_ids"]

        started = client.post(f"/api/batches/{batch['id']}/run", json={})
        assert started.status_code == 200
        assert started.json()["succeeded"] == 3
        jobs = _wait_for_terminal_jobs(client, job_ids)
        assert [job["status"] for job in jobs].count("failed") == 1
        assert [job["status"] for job in jobs].count("completed") == 2

        retried = client.post(f"/api/batches/{batch['id']}/retry-failed")
        assert retried.status_code == 200
        assert retried.json()["succeeded"] == 1
        assert retried.json()["failed"] == 2
        _wait_for_terminal_jobs(client, job_ids)

        update = client.post(
            "/api/jobs/bulk-actions",
            json={
                "action": "update_config",
                "job_ids": [job_ids[0], "missing-job"],
                "step": "translation",
                "config": {
                    "target_language": "ko",
                    "provider": "codex_spark",
                    "timeout_seconds": 300,
                },
            },
        )
        assert update.status_code == 200
        assert update.json()["succeeded"] == 1
        assert update.json()["failed"] == 1

        detached = client.delete(f"/api/batches/{batch['id']}")
        assert detached.status_code == 200
        assert detached.json()["delete_jobs"] is False
        assert client.get(f"/api/batches/{batch['id']}").status_code == 404
        assert client.get(f"/api/jobs/{job_ids[0]}").json()["batch_id"] is None

        disposable = _create_batch(client, [good_one])
        disposable_batch = disposable["batch"]
        disposable_job = disposable_batch["job_ids"][0]
        exported_srt = good_one.with_suffix(".srt")
        exported_srt.write_text("keep me", encoding="utf-8")
        deleted = client.delete(
            f"/api/batches/{disposable_batch['id']}",
            params={"delete_jobs": "true"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["delete_jobs"] is True
        assert client.get(f"/api/jobs/{disposable_job}").status_code == 404
        assert exported_srt.read_text(encoding="utf-8") == "keep me"

        single = _create_batch(client, [good_two])
        single_batch = single["batch"]
        single_job = single_batch["job_ids"][0]
        assert client.delete(f"/api/jobs/{single_job}").status_code == 200
        remaining = client.get(f"/api/batches/{single_batch['id']}").json()
        assert remaining["job_ids"] == []
        empty_run = client.post(f"/api/batches/{single_batch['id']}/run")
        assert empty_run.status_code == 200
        assert empty_run.json()["results"] == []


def test_deleting_batch_with_active_jobs_detaches_them_before_group_removal(
    tmp_path: Path,
) -> None:
    pipeline = BlockingPipeline()
    app = create_app(
        data_dir=tmp_path / "data",
        pipeline=pipeline,  # type: ignore[arg-type]
        scheduler_settings=SchedulerSettings(worker_concurrency=1),
    )
    videos = [
        _video(tmp_path / "active.mp4"),
        _video(tmp_path / "queued.mp4"),
    ]
    with TestClient(app) as client:
        created = _create_batch(client, videos)
        batch = created["batch"]
        assert isinstance(batch, dict)
        job_ids = batch["job_ids"]
        assert client.post(f"/api/batches/{batch['id']}/run", json={}).status_code == 200
        assert pipeline.started.wait(timeout=1)

        deleted = client.delete(
            f"/api/batches/{batch['id']}",
            params={"delete_jobs": "true"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert deleted.json()["delete_jobs"] is True
        assert deleted.json()["results"]
        assert all(not result["ok"] for result in deleted.json()["results"])
        assert all(
            result["job"]["batch_id"] is None
            for result in deleted.json()["results"]
        )
        assert client.get(f"/api/batches/{batch['id']}").status_code == 404

        for job_id in job_ids:
            job = client.get(f"/api/jobs/{job_id}")
            assert job.status_code == 200
            assert job.json()["batch_id"] is None
            assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200

        for _ in range(100):
            states = [
                client.get(f"/api/jobs/{job_id}").json()["status"]
                for job_id in job_ids
            ]
            if states == ["cancelled", "cancelled"]:
                break
            time.sleep(0.01)
        assert states == ["cancelled", "cancelled"]
