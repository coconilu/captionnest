from __future__ import annotations

import asyncio
import json
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

        disposable_source = _video(tmp_path / "disposable.mp4")
        disposable = _create_batch(client, [disposable_source])
        disposable_batch = disposable["batch"]
        disposable_job = disposable_batch["job_ids"][0]
        exported_srt = disposable_source.with_suffix(".srt")
        exported_srt.write_text("keep me", encoding="utf-8")
        deleted = client.delete(
            f"/api/batches/{disposable_batch['id']}",
            params={"delete_jobs": "true"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["delete_jobs"] is True
        assert client.get(f"/api/jobs/{disposable_job}").status_code == 404
        assert exported_srt.read_text(encoding="utf-8") == "keep me"

        single = _create_batch(client, [_video(tmp_path / "single.mp4")])
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


def test_summary_cursor_keeps_snapshot_order_filters_and_original_watermark(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        created_ids: list[str] = []
        for name in ("alpha-oldest.mp4", "alpha-middle.mp4", "alpha-newest.mp4"):
            response = client.post(
                "/api/jobs",
                json={"video_path": str(_video(tmp_path / name))},
            )
            assert response.status_code == 200
            created_ids.append(response.json()["id"])
            time.sleep(0.002)
        beta = client.post(
            "/api/jobs",
            json={"video_path": str(_video(tmp_path / "beta-newest.mp4"))},
        )
        assert beta.status_code == 200

        first = client.get("/api/jobs", params={"limit": 1, "q": "alpha"})
        assert first.status_code == 200
        first_page = first.json()
        assert [item["id"] for item in first_page["items"]] == [created_ids[2]]

        middle = client.get(f"/api/jobs/{created_ids[1]}").json()
        translation = next(
            step for step in middle["steps"] if step["id"] == "translation"
        )
        config = dict(translation["config"])
        config["target_language"] = "ko"
        updated = client.patch(
            f"/api/jobs/{created_ids[1]}/steps/translation/config",
            json={"config": config},
        )
        assert updated.status_code == 200

        second = client.get(
            "/api/jobs",
            params={"limit": 1, "cursor": first_page["next_cursor"]},
        )
        assert second.status_code == 200
        second_page = second.json()
        assert [item["id"] for item in second_page["items"]] == [created_ids[1]]
        assert second_page["server_time"] == first_page["server_time"]
        assert all(
            item["source_name"].startswith("alpha")
            for item in second_page["items"]
        )

        mismatched = client.get(
            "/api/jobs",
            params={
                "limit": 1,
                "cursor": first_page["next_cursor"],
                "q": "beta",
            },
        )
        assert mismatched.status_code == 400

        incremental = client.get(
            "/api/jobs",
            params={"limit": 20, "updated_after": first_page["server_time"]},
        )
        assert incremental.status_code == 200
        assert created_ids[1] in {
            item["id"] for item in incremental.json()["items"]
        }


def test_incremental_cursor_excludes_updates_after_first_page_watermark(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=ImmediatePipeline())  # type: ignore[arg-type]

    def update_target(client: TestClient, job_id: str, target: str) -> None:
        job = client.get(f"/api/jobs/{job_id}").json()
        translation = next(
            step for step in job["steps"] if step["id"] == "translation"
        )
        config = dict(translation["config"])
        config["target_language"] = target
        response = client.patch(
            f"/api/jobs/{job_id}/steps/translation/config",
            json={"config": config},
        )
        assert response.status_code == 200

    with TestClient(app) as client:
        created_ids = []
        for name in ("oldest.mp4", "middle.mp4", "newest.mp4"):
            response = client.post(
                "/api/jobs",
                json={"video_path": str(_video(tmp_path / name))},
            )
            assert response.status_code == 200
            created_ids.append(response.json()["id"])

        baseline = client.get("/api/jobs", params={"limit": 1}).json()[
            "server_time"
        ]
        update_target(client, created_ids[2], "en")
        update_target(client, created_ids[1], "ko")

        first = client.get(
            "/api/jobs",
            params={"limit": 1, "updated_after": baseline},
        )
        assert first.status_code == 200
        first_page = first.json()
        assert first_page["total"] == 2
        assert [item["id"] for item in first_page["items"]] == [created_ids[2]]
        watermark = first_page["server_time"]

        update_target(client, created_ids[0], "en")

        second = client.get(
            "/api/jobs",
            params={"limit": 1, "cursor": first_page["next_cursor"]},
        )
        assert second.status_code == 200
        second_page = second.json()
        assert second_page["total"] == 2
        assert second_page["server_time"] == watermark
        assert second_page["has_more"] is False
        assert [item["id"] for item in second_page["items"]] == [created_ids[1]]

        next_round = client.get(
            "/api/jobs",
            params={"limit": 20, "updated_after": watermark},
        )
        assert next_round.status_code == 200
        assert next_round.json()["total"] == 1
        assert [item["id"] for item in next_round.json()["items"]] == [
            created_ids[0]
        ]


def test_incremental_cursor_freezes_unread_member_summary(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=ImmediatePipeline())  # type: ignore[arg-type]

    def update_target(
        client: TestClient,
        job_id: str,
        target: str,
    ) -> dict[str, object]:
        job = client.get(f"/api/jobs/{job_id}").json()
        translation = next(
            step for step in job["steps"] if step["id"] == "translation"
        )
        config = dict(translation["config"])
        config["target_language"] = target
        response = client.patch(
            f"/api/jobs/{job_id}/steps/translation/config",
            json={"config": config},
        )
        assert response.status_code == 200
        return response.json()

    with TestClient(app) as client:
        created_ids = []
        for name in ("member-oldest.mp4", "member-middle.mp4", "member-newest.mp4"):
            response = client.post(
                "/api/jobs",
                json={"video_path": str(_video(tmp_path / name))},
            )
            assert response.status_code == 200
            created_ids.append(response.json()["id"])

        baseline = client.get("/api/jobs", params={"limit": 1}).json()[
            "server_time"
        ]
        update_target(client, created_ids[2], "en")
        middle_at_snapshot = update_target(client, created_ids[1], "ko")

        first = client.get(
            "/api/jobs",
            params={"limit": 1, "updated_after": baseline},
        ).json()
        assert first["total"] == 2
        assert [item["id"] for item in first["items"]] == [created_ids[2]]
        watermark = first["server_time"]

        middle_after_snapshot = update_target(client, created_ids[1], "en")
        assert middle_after_snapshot["updated_at"] != middle_at_snapshot["updated_at"]

        second = client.get(
            "/api/jobs",
            params={"limit": 1, "cursor": first["next_cursor"]},
        )
        assert second.status_code == 200
        second_page = second.json()
        assert second_page["total"] == 2
        assert second_page["server_time"] == watermark
        assert second_page["has_more"] is False
        assert [item["id"] for item in second_page["items"]] == [created_ids[1]]
        assert second_page["items"][0]["updated_at"] == middle_at_snapshot[
            "updated_at"
        ]

        next_round = client.get(
            "/api/jobs",
            params={"limit": 20, "updated_after": watermark},
        )
        assert next_round.status_code == 200
        assert next_round.json()["total"] == 1
        assert [item["id"] for item in next_round.json()["items"]] == [
            created_ids[1]
        ]
        assert next_round.json()["items"][0]["updated_at"] == middle_after_snapshot[
            "updated_at"
        ]


def test_summary_snapshot_cursor_expires_and_obeys_memory_bounds(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        for name in (
            "group-a-one.mp4",
            "group-a-two.mp4",
            "group-b-one.mp4",
            "group-b-two.mp4",
        ):
            response = client.post(
                "/api/jobs",
                json={"video_path": str(_video(tmp_path / name))},
            )
            assert response.status_code == 200

        manager = app.state.job_manager
        first = client.get(
            "/api/jobs",
            params={"limit": 1, "q": "group-a"},
        ).json()
        assert first["next_cursor"]
        snapshot = next(iter(manager._summary_snapshots.values()))
        assert len(snapshot.snapshot_id) == 32
        assert all(
            {"logs", "steps", "api_key"}.isdisjoint(item.model_dump())
            for item in snapshot.items
        )

        manager._summary_snapshot_clock = (
            lambda: snapshot.expires_at_monotonic + 0.001
        )
        expired = client.get(
            "/api/jobs",
            params={"limit": 1, "cursor": first["next_cursor"]},
        )
        assert expired.status_code == 400
        assert "已过期或已被淘汰" in expired.json()["detail"]
        manager._summary_snapshot_clock = time.monotonic

        manager._summary_snapshot_max_entries = 1
        older = client.get(
            "/api/jobs",
            params={"limit": 1, "q": "group-a"},
        ).json()
        newer = client.get(
            "/api/jobs",
            params={"limit": 1, "q": "group-b"},
        ).json()
        evicted = client.get(
            "/api/jobs",
            params={"limit": 1, "cursor": older["next_cursor"]},
        )
        assert evicted.status_code == 400
        assert "已过期或已被淘汰" in evicted.json()["detail"]
        assert client.get(
            "/api/jobs",
            params={"limit": 1, "cursor": newer["next_cursor"]},
        ).status_code == 200

        manager._summary_snapshot_max_entries = 64
        manager._summary_snapshot_max_items = 1
        over_limit = client.get(
            "/api/jobs",
            params={"limit": 1, "q": "group-a"},
        )
        assert over_limit.status_code == 400
        assert "超过快照上限" in over_limit.json()["detail"]


def test_output_claims_span_batches_and_reject_non_file_targets(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    output = tmp_path / "output"
    output.mkdir()
    first = _video(tmp_path / "a" / "same.mp4")
    second = _video(tmp_path / "b" / "same.mp4")
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]

    with TestClient(app) as client:
        first_batch = _create_batch(
            client,
            [first],
            config={"export": {"output_directory": str(output)}},
        )
        assert first_batch["created_count"] == 1

        conflicting_payload = {
            "sources": [{"video_path": str(second)}],
            "config": {"export": {"output_directory": str(output)}},
        }
        preflight = client.post("/api/batches/preflight", json=conflicting_payload)
        assert preflight.status_code == 200
        assert preflight.json()["valid_count"] == 0
        assert "output_conflict" in {
            issue["code"] for issue in preflight.json()["items"][0]["issues"]
        }
        rejected = client.post("/api/batches", json=conflicting_payload)
        assert rejected.status_code == 200
        assert rejected.json()["created_count"] == 0
        assert rejected.json()["batch"] is None

        direct = client.post(
            "/api/jobs",
            json={
                "video_path": str(second),
                "export": {"output_directory": str(output)},
            },
        )
        assert direct.status_code == 400

        alternate_output = tmp_path / "alternate-output"
        alternate = client.post(
            "/api/jobs",
            json={
                "video_path": str(second),
                "export": {"output_directory": str(alternate_output)},
            },
        )
        assert alternate.status_code == 200
        alternate_record = app.state.job_manager._record(alternate.json()["id"])
        alternate_record.export = alternate_record.export.model_copy(
            update={"output_directory": str(output)}
        )
        alternate_record._persist()
        run_conflict = client.post(f"/api/jobs/{alternate.json()['id']}/run", json={})
        assert run_conflict.status_code == 400

        target_directory_source = _video(tmp_path / "lesson.mp4")
        (output / "lesson.srt").mkdir()
        directory_target = client.post(
            "/api/batches/preflight",
            json={
                "sources": [{"video_path": str(target_directory_source)}],
                "config": {"export": {"output_directory": str(output)}},
            },
        )
        assert directory_target.status_code == 200
        assert directory_target.json()["valid_count"] == 0
        assert "invalid_output" in {
            issue["code"]
            for issue in directory_target.json()["items"][0]["issues"]
        }

        output_file = tmp_path / "not-a-directory"
        output_file.write_text("file", encoding="utf-8")
        invalid_directory = client.post(
            "/api/batches/preflight",
            json={
                "sources": [{"video_path": str(_video(tmp_path / "other.mp4"))}],
                "config": {"export": {"output_directory": str(output_file)}},
            },
        )
        assert invalid_directory.status_code == 200
        assert invalid_directory.json()["valid_count"] == 0
        assert "invalid_output" in {
            issue["code"]
            for issue in invalid_directory.json()["items"][0]["issues"]
        }


def test_create_failures_compensate_job_and_batch_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    source = _video(tmp_path / "create-failure.mp4")

    with TestClient(app) as client:
        job_store = app.state.job_store
        original_save_job = job_store.save_job

        def save_job_then_fail(job_id: str, payload: object) -> None:
            original_save_job(job_id, payload)
            raise OSError("injected job save failure")

        monkeypatch.setattr(job_store, "save_job", save_job_then_fail)
        failed_job = _create_batch(client, [source])
        assert failed_job["created_count"] == 0
        assert failed_job["batch"] is None
        assert client.get("/api/jobs").json() == []
        assert not list((data_dir / "jobs").glob("*/job.json"))

        monkeypatch.setattr(job_store, "save_job", original_save_job)
        batch_store = app.state.batch_store
        original_save_batch = batch_store.save
        save_calls = 0

        def save_batch_then_fail_association(batch: object) -> None:
            nonlocal save_calls
            save_calls += 1
            original_save_batch(batch)
            if save_calls == 2:
                raise OSError("injected batch association failure")

        monkeypatch.setattr(batch_store, "save", save_batch_then_fail_association)
        failed_association = _create_batch(
            client,
            [_video(tmp_path / "association-failure.mp4")],
        )
        assert failed_association["created_count"] == 0
        assert failed_association["batch"] is None
        assert client.get("/api/jobs").json() == []

    reloaded = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(reloaded) as client:
        assert client.get("/api/jobs").json() == []
        assert client.get("/api/batches").json() == []


def test_auto_start_queue_write_failure_rolls_back_and_remains_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    injected = False

    def fail_before_queue_write(job_id: str, payload: dict[str, object]) -> None:
        nonlocal injected
        if payload.get("status") == "queued" and not injected:
            injected = True
            raise OSError("injected queue write failure")
        original_save_job(job_id, payload)

    with TestClient(app) as client:
        store = app.state.job_store
        original_save_job = store.save_job
        monkeypatch.setattr(store, "save_job", fail_before_queue_write)
        result = _create_batch(
            client,
            [_video(tmp_path / "queue-write-failure.mp4")],
            auto_start=True,
        )
        assert injected is True
        assert result["created_count"] == 1
        assert result["results"][0]["ok"] is True
        assert "自动启动失败" in result["results"][0]["error"]
        job_id = result["results"][0]["job"]["id"]
        record = app.state.job_manager._record(job_id)
        assert record.status.value == "draft"
        assert record.queue_status.value == "draft"
        assert app.state.job_manager.scheduler.is_active(job_id) is False
        assert job_id not in app.state.job_manager.scheduler.completions
        assert store.load_job(job_id) == record.to_payload()

    retry_pipeline = BlockingPipeline()
    reloaded = create_app(data_dir=data_dir, pipeline=retry_pipeline)  # type: ignore[arg-type]
    with TestClient(reloaded) as client:
        job_id = client.get("/api/jobs").json()[0]["id"]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "draft"
        rerun = client.post(f"/api/jobs/{job_id}/run", json={})
        assert rerun.status_code == 200
        assert retry_pipeline.started.wait(timeout=1)
        assert reloaded.state.job_manager.scheduler.is_active(job_id) is True
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200


def test_auto_start_queue_after_write_error_commits_real_active_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = BlockingPipeline()
    app = create_app(data_dir=tmp_path / "data", pipeline=pipeline)  # type: ignore[arg-type]
    injected = False

    def fail_after_queue_write(job_id: str, payload: dict[str, object]) -> None:
        nonlocal injected
        original_save_job(job_id, payload)
        if payload.get("status") == "queued" and not injected:
            injected = True
            raise OSError("injected error after committed queue write")

    with TestClient(app) as client:
        store = app.state.job_store
        original_save_job = store.save_job
        monkeypatch.setattr(store, "save_job", fail_after_queue_write)
        result = _create_batch(
            client,
            [_video(tmp_path / "queue-after-write.mp4")],
            auto_start=True,
        )
        assert injected is True
        assert result["created_count"] == 1
        assert result["failed_count"] == 0
        assert result["results"][0]["ok"] is True
        assert result["results"][0]["error"] is None
        job_id = result["results"][0]["job"]["id"]
        assert pipeline.started.wait(timeout=1)
        record = app.state.job_manager._record(job_id)
        assert record.status.value == "running"
        assert app.state.job_manager.scheduler.is_active(job_id) is True
        assert job_id in app.state.job_manager.scheduler.completions
        assert store.load_job(job_id) == record.to_payload()
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200


def test_delete_failure_keeps_job_visible_and_restart_consistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        created = _create_batch(client, [_video(tmp_path / "locked.mp4")])
        batch = created["batch"]
        job_id = batch["job_ids"][0]

        def fail_delete(_job_id: str) -> None:
            raise OSError("injected Windows directory lock")

        monkeypatch.setattr(app.state.job_store, "delete_job", fail_delete)
        deleted = client.post(
            "/api/jobs/bulk-actions",
            json={"action": "delete", "job_ids": [job_id]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["failed"] == 1
        assert deleted.json()["results"][0]["job"]["id"] == job_id
        assert client.get(f"/api/jobs/{job_id}").status_code == 200
        batch_view = client.get(f"/api/batches/{batch['id']}").json()
        assert batch_view["job_ids"] == [job_id]
        assert batch_view["status_summary"]["total"] == 1

    reloaded = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(reloaded) as client:
        assert client.get(f"/api/jobs/{job_id}").status_code == 200
        assert client.get(f"/api/batches/{batch['id']}").json()["job_ids"] == [
            job_id
        ]


def test_startup_reconciles_job_and_batch_membership_bidirectionally(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        created = _create_batch(
            client,
            [
                _video(tmp_path / "kept.mp4"),
                _video(tmp_path / "orphaned.mp4"),
            ],
        )
        batch = created["batch"]
        kept_job, orphaned_job = batch["job_ids"]

    batch_file = data_dir / "batches" / batch["id"] / "batch.json"
    batch_payload = json.loads(batch_file.read_text(encoding="utf-8"))
    batch_payload["job_ids"] = [orphaned_job]
    batch_file.write_text(json.dumps(batch_payload), encoding="utf-8")

    orphaned_file = data_dir / "jobs" / orphaned_job / "job.json"
    orphaned_payload = json.loads(orphaned_file.read_text(encoding="utf-8"))
    orphaned_payload["batch_id"] = "missing-batch"
    orphaned_file.write_text(json.dumps(orphaned_payload), encoding="utf-8")

    reloaded = create_app(data_dir=data_dir, pipeline=ImmediatePipeline())  # type: ignore[arg-type]
    with TestClient(reloaded) as client:
        repaired_batch = client.get(f"/api/batches/{batch['id']}").json()
        assert repaired_batch["job_ids"] == [kept_job]
        assert client.get(f"/api/jobs/{kept_job}").json()["batch_id"] == batch["id"]
        assert client.get(f"/api/jobs/{orphaned_job}").json()["batch_id"] is None
