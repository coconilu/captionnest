import asyncio
from pathlib import Path

import pytest

from sublingo_local.asr.base import ASRProvider, TranscriptionResult
from sublingo_local.jobs import JobManager, JobRecord, ProcessingPipeline
from sublingo_local.models import (
    JobCreateRequest,
    JobStage,
    JobStatus,
    ResolvedSource,
    SourceKind,
    SubtitleSegment,
    TargetLanguage,
    TranslatedItem,
)
from sublingo_local.translation.base import TranslationProvider


class FakeASR(ASRProvider):
    def transcribe(self, audio_path, *, language, settings, on_progress=None):  # type: ignore[no-untyped-def]
        assert audio_path.exists()
        assert audio_path.suffix == ".mp4"
        assert language == "auto"
        if on_progress:
            on_progress(1.0)
        return TranscriptionResult(
            language="ja",
            segments=[
                SubtitleSegment(id="seg-000001", start_ms=100, end_ms=1_200, text="こんにちは")
            ],
        )


class FakeTranslator(TranslationProvider):
    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language
    ):
        assert source_language == "ja"
        assert target_language == TargetLanguage.ZH_CN
        return [TranslatedItem(id=item.id, translated_text="你好") for item in items]


def test_path_pipeline_writes_one_bilingual_subtitle_next_to_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"fake video")

    monkeypatch.setattr(
        "sublingo_local.jobs.create_translation_provider", lambda settings: FakeTranslator()
    )
    request = JobCreateRequest(
        video_path=str(video),
        translation={"provider": "codex_spark"},
    )
    record = JobRecord(
        id="job-1",
        request=request,
        source=ResolvedSource(kind=SourceKind.PATH, path=video, name=video.name),
    )
    pipeline = ProcessingPipeline(tmp_path / "temp", asr_factory=FakeASR)

    asyncio.run(pipeline.run(record))

    subtitle = tmp_path / "movie.srt"
    assert record.subtitle_path == str(subtitle)
    assert list(tmp_path.glob("*.srt")) == [subtitle]
    assert "こんにちは\n你好" in subtitle.read_text(encoding="utf-8-sig")
    assert record.to_view().target_language == TargetLanguage.ZH_CN


def test_pipeline_rejects_same_detected_and_target_language_before_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "english.mp4"
    video.write_bytes(b"fake video")

    class EnglishASR(ASRProvider):
        def transcribe(  # type: ignore[no-untyped-def]
            self, audio_path, *, language, settings, on_progress=None
        ):
            assert language == "auto"
            return TranscriptionResult(
                language="en",
                segments=[
                    SubtitleSegment(id="seg-000001", start_ms=100, end_ms=1_200, text="Hello")
                ],
            )

    def unexpected_provider(settings):  # type: ignore[no-untyped-def]
        pytest.fail("同语言任务不应创建翻译 Provider")

    monkeypatch.setattr("sublingo_local.jobs.create_translation_provider", unexpected_provider)
    request = JobCreateRequest(video_path=str(video), target_language="en")
    record = JobRecord(
        id="job-same-language",
        request=request,
        source=ResolvedSource(kind=SourceKind.PATH, path=video, name=video.name),
    )
    pipeline = ProcessingPipeline(tmp_path / "temp", asr_factory=EnglishASR)

    with pytest.raises(ValueError, match="源语言与目标语言相同"):
        asyncio.run(pipeline.run(record))

    assert record.detected_language == "en"
    assert record.subtitle_path is None
    assert not list(tmp_path.glob("*.srt"))


def test_job_request_exposes_only_target_language() -> None:
    request = JobCreateRequest(video_path="movie.mp4")

    assert request.target_language == TargetLanguage.ZH_CN
    assert "source_language" not in JobCreateRequest.model_fields
    assert "output" not in JobCreateRequest.model_fields
    for value in ("zh-CN", "en", "ko"):
        assert JobCreateRequest(video_path="movie.mp4", target_language=value)


def _record_with_api_key(tmp_path: Path, secret: str) -> JobRecord:
    video = tmp_path / "secret-job.mp4"
    video.write_bytes(b"fake video")
    request = JobCreateRequest(
        video_path=str(video),
        translation={"provider": "deepseek", "api_key": secret},
    )
    return JobRecord(
        id="job-with-secret",
        request=request,
        source=ResolvedSource(kind=SourceKind.PATH, path=video, name=video.name),
    )


def test_job_manager_clears_api_key_after_success(tmp_path: Path) -> None:
    class SuccessfulPipeline:
        async def run(self, record):  # type: ignore[no-untyped-def]
            record.update(
                status=JobStatus.COMPLETED,
                stage=JobStage.COMPLETED,
                progress=100,
            )

    record = _record_with_api_key(tmp_path, "success-secret")
    manager = JobManager(None, SuccessfulPipeline())  # type: ignore[arg-type]

    asyncio.run(manager._run(record))

    assert record.request.translation.api_key is None
    assert record.to_view().status == JobStatus.COMPLETED


def test_job_manager_redacts_failure_before_clearing_api_key(tmp_path: Path) -> None:
    secret = "failure-secret"

    class FailedPipeline:
        async def run(self, record):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"provider failed with {secret}")

    record = _record_with_api_key(tmp_path, secret)
    manager = JobManager(None, FailedPipeline())  # type: ignore[arg-type]

    asyncio.run(manager._run(record))

    view = record.to_view()
    assert record.request.translation.api_key is None
    assert view.status == JobStatus.FAILED
    assert secret not in (view.error or "")
    assert "***" in (view.error or "")
    assert all(secret not in log.message for log in view.logs)


def test_job_manager_clears_api_key_when_cancelled(tmp_path: Path) -> None:
    class CancelledPipeline:
        async def run(self, record):  # type: ignore[no-untyped-def]
            raise asyncio.CancelledError

    async def scenario(record: JobRecord) -> None:
        manager = JobManager(None, CancelledPipeline())  # type: ignore[arg-type]
        with pytest.raises(asyncio.CancelledError):
            await manager._run(record)

    record = _record_with_api_key(tmp_path, "cancelled-secret")
    asyncio.run(scenario(record))

    assert record.request.translation.api_key is None
    assert record.to_view().status == JobStatus.CANCELLED
