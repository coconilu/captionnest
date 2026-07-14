import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sublingo_local.app import create_app
from sublingo_local.model_manager import ModelView
from sublingo_local.models import JobStage, JobStatus


class FakePipeline:
    async def run(self, record):  # type: ignore[no-untyped-def]
        record.subtitle_path = str(record.source.path.with_suffix(".srt"))
        record.detected_language = "en"
        record.update(
            status=JobStatus.COMPLETED,
            stage=JobStage.COMPLETED,
            progress=100,
            message="fake complete",
        )


@pytest.fixture(autouse=True)
def clear_desktop_session_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTIONNEST_SESSION_TOKEN", raising=False)


def test_health_capabilities_upload_and_job_do_not_echo_api_key(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        capabilities = client.get("/api/capabilities").json()
        assert capabilities["asr"]["provider"] == "faster-whisper"
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
                    "api_key": secret,
                },
            },
        )
        assert response.status_code == 200
        assert secret not in response.text
        job_id = response.json()["id"]
        for _ in range(50):
            job = client.get(f"/api/jobs/{job_id}")
            if job.json()["status"] == "completed":
                break
            time.sleep(0.01)
        assert job.json()["progress"] == 100
        assert job.json()["target_language"] == "ko"
        assert job.json()["detected_language"] == "en"
        assert job.json()["subtitle_path"].endswith("lesson.srt")
        assert "source_subtitle_path" not in job.json()
        assert "translated_subtitle_path" not in job.json()
        assert secret not in job.text


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


def test_model_catalog_and_download_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        catalog = client.get("/api/models")
        assert catalog.status_code == 200
        assert catalog.json()["model_root"] == str((tmp_path / "data" / "models").resolve())
        assert {item["status"] for item in catalog.json()["items"]} == {"missing"}

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
