import time
from pathlib import Path

from fastapi.testclient import TestClient

from sublingo_local.app import create_app
from sublingo_local.models import JobStage, JobStatus


class FakePipeline:
    async def run(self, record):  # type: ignore[no-untyped-def]
        record.source_subtitle_path = str(record.source.path.with_suffix(".en.srt"))
        record.translated_subtitle_path = str(record.source.path.with_suffix(".zh-CN.srt"))
        record.detected_language = "en"
        record.update(
            status=JobStatus.COMPLETED,
            stage=JobStage.COMPLETED,
            progress=100,
            message="fake complete",
        )


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
                "source_language": "en",
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
        assert secret not in job.text


def test_job_rejects_missing_or_ambiguous_source(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", pipeline=FakePipeline())  # type: ignore[arg-type]
    with TestClient(app) as client:
        missing = client.post(
            "/api/jobs", json={"translation": {"provider": "codex_spark"}}
        )
        assert missing.status_code == 422

