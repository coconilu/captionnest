import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sublingo_local.app import create_app
from sublingo_local.jobs import STEP_ORDER
from sublingo_local.model_manager import ModelView
from sublingo_local.models import (
    ASR_HOTWORD_MAX_ENTRIES,
    ASR_HOTWORD_MAX_ENTRY_CHARACTERS,
    ASR_HOTWORD_MAX_TOTAL_CHARACTERS,
    JobStep,
    StepArtifactView,
    StepStatus,
)


class FakePipeline:
    async def run_from(  # type: ignore[no-untyped-def]
        self, record, start_step, *, api_key=None, continue_pipeline=True
    ):
        selected = (
            STEP_ORDER[STEP_ORDER.index(start_step) :]
            if continue_pipeline
            else (start_step,)
        )
        for step in selected:
            record.begin_step(step, f"fake {step.value}")
            if step == JobStep.TRANSCRIPTION:
                record.set_detected_language("en")
            path = (
                record.source.path.with_suffix(".srt")
                if step == JobStep.EXPORT
                else Path(f"{step.value}.json")
            )
            record.complete_step(
                step,
                StepArtifactView(
                    id=f"{step.value}-1",
                    step=step,
                    path=str(path),
                    fingerprint=f"fingerprint-{step.value}",
                    config_fingerprint=f"config-{step.value}",
                ),
                f"fake {step.value} complete",
            )
        if all(record.steps[step].status == StepStatus.SUCCEEDED for step in STEP_ORDER):
            record.mark_complete()
        else:
            record.mark_paused()


@pytest.fixture(autouse=True)
def clear_desktop_session_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTIONNEST_SESSION_TOKEN", raising=False)


def test_health_capabilities_upload_and_job_do_not_echo_api_key(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        capabilities = client.get("/api/capabilities").json()
        assert capabilities["asr"]["provider"] == "faster-whisper"
        assert {item["id"] for item in capabilities["asr"]["providers"]} == {
            "faster_whisper"
        }
        assert capabilities["asr"]["models"] == [
            "small",
            "medium",
            "large-v3-turbo",
            "large-v3",
        ]
        assert {item["id"] for item in capabilities["translation"]["providers"]} == {
            "codex_spark",
            "lmstudio",
            "deepseek",
        }

        upload = client.post(
            "/api/uploads", files={"file": ("lesson.mp4", b"not-a-real-video", "video/mp4")}
        )
        assert upload.status_code == 200
        upload_data = upload.json()
        upload_path = Path(upload_data["path"])
        assert upload_path.read_bytes() == b"not-a-real-video"
        assert tmp_path / "data" / "uploads" in upload_path.parents

        secret = "deepseek-secret-that-must-not-leak"
        response = client.post(
            "/api/jobs",
            json={
                "upload_id": upload_data["upload_id"],
                "target_language": "ko",
                "translation": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                },
            },
        )
        assert response.status_code == 200
        assert secret not in response.text
        transcription = next(
            step
            for step in response.json()["steps"]
            if step["id"] == "transcription"
        )
        assert transcription["config"]["dynamic_chunking"] is True
        assert transcription["config"]["selective_retry"] is True
        job_id = response.json()["id"]
        assert response.json()["status"] == "draft"

        run = client.post(f"/api/jobs/{job_id}/run", json={"api_key": secret})
        assert run.status_code == 200
        assert run.json()["status"] in {"queued", "running", "completed"}
        assert secret not in run.text
        for _ in range(50):
            job = client.get(f"/api/jobs/{job_id}")
            if job.json()["status"] == "completed":
                break
            time.sleep(0.01)
        assert job.json()["progress"] == 100
        assert job.json()["target_language"] == "ko"
        assert job.json()["detected_language"] == "en"
        assert job.json()["asr_provider"] == "faster_whisper"
        assert job.json()["subtitle_path"].endswith("lesson.srt")
        assert "source_subtitle_path" not in job.json()
        assert "translated_subtitle_path" not in job.json()
        assert secret not in job.text
        assert secret not in app.state.job_store.job_file(job_id).read_text(encoding="utf-8")


def test_job_rejects_missing_or_ambiguous_source(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        missing = client.post(
            "/api/jobs", json={"translation": {"provider": "codex_spark"}}
        )
        assert missing.status_code == 422

        ambiguous = client.post(
            "/api/jobs",
            json={
                "video_path": str(tmp_path / "lesson.mp4"),
                "upload_id": "also-an-upload",
                "translation": {"provider": "codex_spark"},
            },
        )
        assert ambiguous.status_code == 422


def test_job_rejects_removed_or_unknown_asr_models(tmp_path: Path) -> None:
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fake video")
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]

    invalid_settings = (
        {"provider": "faster_whisper", "model": "qwen3-asr-1.7b"},
        {"provider": "qwen3_asr", "model": "qwen3-asr-1.7b"},
        {"provider": "faster_whisper", "model": "not-a-model"},
    )
    with TestClient(app) as client:
        for asr in invalid_settings:
            response = client.post(
                "/api/jobs",
                json={"video_path": str(video), "asr": asr},
            )
            assert response.status_code == 422

        for model in ("small", "medium", "large-v3-turbo", "large-v3"):
            response = client.post(
                "/api/jobs",
                json={
                    "video_path": str(video),
                    "asr": {"provider": "faster_whisper", "model": model},
                },
            )
            assert response.status_code == 200


def test_job_hotwords_schema_normalization_and_safe_validation(tmp_path: Path) -> None:
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fake video")
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]

    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()["components"]["schemas"][
            "ASRSettings"
        ]["properties"]["hotwords"]
        assert schema["type"] == "array"
        assert schema["maxItems"] == ASR_HOTWORD_MAX_ENTRIES
        assert schema["max_item_characters"] == ASR_HOTWORD_MAX_ENTRY_CHARACTERS
        assert schema["max_total_characters"] == ASR_HOTWORD_MAX_TOTAL_CHARACTERS

        created = client.post(
            "/api/jobs",
            json={
                "video_path": str(video),
                "asr": {
                    "hotwords": [
                        "  CaptionNest  ",
                        "",
                        "初音未来",
                        "CaptionNest",
                    ]
                },
            },
        )
        assert created.status_code == 200
        transcription = next(
            step
            for step in created.json()["steps"]
            if step["id"] == "transcription"
        )
        assert transcription["config"]["hotwords"] == ["CaptionNest", "初音未来"]

        rejected_hotword = "private-" + (
            "x" * ASR_HOTWORD_MAX_ENTRY_CHARACTERS
        )
        rejected = client.post(
            "/api/jobs",
            json={
                "video_path": str(video),
                "asr": {"hotwords": [rejected_hotword]},
            },
        )
        assert rejected.status_code == 422
        assert rejected_hotword not in rejected.text
        assert "单个提示词不能超过" in rejected.json()["detail"][0]["msg"]
        assert "input" not in rejected.json()["detail"][0]

        invalid_config = dict(transcription["config"])
        invalid_config["hotwords"] = [rejected_hotword]
        rejected_update = client.patch(
            f"/api/jobs/{created.json()['id']}/steps/transcription/config",
            json={"config": invalid_config},
        )
        assert rejected_update.status_code == 400
        assert rejected_hotword not in rejected_update.text
        assert "单个提示词不能超过" in rejected_update.json()["detail"]


def test_validation_error_never_echoes_api_key(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    secret = "deepseek-secret-in-invalid-request"

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            json={
                "video_path": str(tmp_path / "lesson.mp4"),
                "upload_id": "ambiguous",
                "translation": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "api_key": secret,
                },
            },
        )

    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_job_step_config_run_and_delete_endpoints(tmp_path: Path) -> None:
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fake video")
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]

    with TestClient(app) as client:
        created = client.post("/api/jobs", json={"video_path": str(video)})
        assert created.status_code == 200
        job_id = created.json()["id"]

        run = client.post(f"/api/jobs/{job_id}/run", json={})
        assert run.status_code == 200
        for _ in range(50):
            job = client.get(f"/api/jobs/{job_id}")
            if job.json()["status"] == "completed":
                break
            time.sleep(0.01)
        assert job.json()["status"] == "completed"

        translation = next(
            step for step in job.json()["steps"] if step["id"] == "translation"
        )
        config = dict(translation["config"])
        config["target_language"] = "ko"
        updated = client.patch(
            f"/api/jobs/{job_id}/steps/translation/config",
            json={"config": config},
        )
        assert updated.status_code == 200
        statuses = {step["id"]: step["status"] for step in updated.json()["steps"]}
        assert statuses == {
            "media": "succeeded",
            "transcription": "succeeded",
            "translation": "stale",
            "export": "stale",
        }

        resumed = client.post(
            f"/api/jobs/{job_id}/steps/translation/run",
            json={},
        )
        assert resumed.status_code == 200
        for _ in range(50):
            job = client.get(f"/api/jobs/{job_id}")
            if job.json()["status"] == "completed":
                break
            time.sleep(0.01)
        assert job.json()["status"] == "completed"
        transcription = next(
            step for step in job.json()["steps"] if step["id"] == "transcription"
        )
        assert len(transcription["attempts"]) == 1

        job_dir = app.state.job_store.job_file(job_id).parent
        deleted = client.delete(f"/api/jobs/{job_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "job_id": job_id}
        assert not job_dir.exists()
        assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_model_catalog_and_download_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        catalog = client.get("/api/models")
        assert catalog.status_code == 200
        assert catalog.json()["model_root"] == str((tmp_path / "data" / "models").resolve())
        assert {item["status"] for item in catalog.json()["items"]} == {"missing"}
        assert {item["id"] for item in catalog.json()["items"]} == {
            "small",
            "medium",
            "large-v3-turbo",
            "large-v3",
        }
        assert {item["provider"] for item in catalog.json()["items"]} == {
            "faster_whisper"
        }

        calls: list[str] = []

        def fake_start_download(model_id: str) -> ModelView:
            calls.append(model_id)
            if model_id != "small":
                raise ValueError("unsupported model")
            return ModelView(
                id=model_id,
                label="small - CPU",
                status="downloading",
                message="starting",
                progress=0,
                recommended_for="cpu",
            )

        monkeypatch.setattr(app.state.model_manager, "start_download", fake_start_download)
        response = client.post("/api/models/small/download")

        assert response.status_code == 200
        assert response.json()["status"] == "downloading"
        assert response.json()["id"] == "small"
        assert calls == ["small"]

        unknown = client.post("/api/models/not-a-model/download")
        assert unknown.status_code == 404


def test_api_requires_matching_desktop_session_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPTIONNEST_SESSION_TOKEN", "desktop-secret")
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]

    with TestClient(app) as client:
        unauthorized = client.get("/api/health")
        assert unauthorized.status_code == 401

        wrong = client.get(
            "/api/health", headers={"X-CaptionNest-Session": "wrong-secret"}
        )
        assert wrong.status_code == 401

        authorized = client.get(
            "/api/health", headers={"X-CaptionNest-Session": "desktop-secret"}
        )
        assert authorized.status_code == 200
        assert authorized.json()["status"] == "ok"
