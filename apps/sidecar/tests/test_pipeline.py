import asyncio
from pathlib import Path

import pytest

from sublingo_local.asr.base import ASRProvider, TranscriptionResult
from sublingo_local.job_store import JobStore
from sublingo_local.jobs import JobManager, JobRecord, ProcessingPipeline
from sublingo_local.models import (
    ASROutputMode,
    JobCreateRequest,
    JobRunRequest,
    JobStep,
    MediaStepSettings,
    SourceKind,
    StepStatus,
    SubtitleSegment,
    TargetLanguage,
    TranslatedItem,
)
from sublingo_local.translation.base import TranslationProvider


class CountingASR(ASRProvider):
    def __init__(self, *, language: str = "ja") -> None:
        self.language = language
        self.calls = 0

    def transcribe(self, audio_path, *, language, settings, on_progress=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        assert audio_path.exists()
        assert audio_path.suffix == ".mp4"
        assert language == "auto"
        if on_progress:
            on_progress(1.0)
        text = "Hello" if self.language == "en" else "こんにちは"
        return TranscriptionResult(
            language=self.language,
            duration_seconds=1.2,
            segments=[
                SubtitleSegment(id="seg-000001", start_ms=100, end_ms=1_200, text=text)
            ],
        )


class CountingTranslator(TranslationProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language
    ):
        self.calls += 1
        return [TranslatedItem(id=item.id, translated_text="你好") for item in items]


def _pipeline(
    tmp_path: Path,
    *,
    asr: CountingASR,
) -> tuple[JobStore, ProcessingPipeline]:
    store = JobStore(tmp_path / "jobs")
    return store, ProcessingPipeline(store.root, asr_factory=lambda: asr, job_store=store)


async def _wait_for_job(manager: JobManager, job_id: str) -> None:
    task = manager._tasks[job_id]
    await task
    await asyncio.sleep(0)


def test_path_pipeline_writes_artifacts_and_one_bilingual_subtitle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR()
    translator = CountingTranslator()
    monkeypatch.setattr(
        "sublingo_local.jobs.create_translation_provider", lambda settings: translator
    )
    store, pipeline = _pipeline(tmp_path, asr=asr)
    record = JobRecord(
        id="job-1",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(video),
            name=video.name,
        ),
    )

    asyncio.run(pipeline.run_from(record, JobStep.MEDIA))

    subtitle = tmp_path / "movie.srt"
    assert record.subtitle_path == str(subtitle)
    assert list(tmp_path.glob("*.srt")) == [subtitle]
    assert "こんにちは\n你好" in subtitle.read_text(encoding="utf-8-sig")
    assert record.to_view().target_language == TargetLanguage.ZH_CN
    assert record.to_view().asr_provider == "faster_whisper"
    assert all(record.steps[step].status == StepStatus.SUCCEEDED for step in JobStep)
    assert asr.calls == 1
    assert translator.calls == 1
    assert store.artifact_path(record.id, "media.json").is_file()
    assert store.artifact_path(record.id, "transcription.json").is_file()
    assert store.artifact_path(record.id, "translation.json").is_file()


def test_translation_failure_can_change_config_and_resume_without_asr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "english.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR(language="en")
    translator = CountingTranslator()

    def provider(settings):  # type: ignore[no-untyped-def]
        return translator

    monkeypatch.setattr("sublingo_local.jobs.create_translation_provider", provider)
    store, pipeline = _pipeline(tmp_path, asr=asr)
    manager = JobManager(None, pipeline, job_store=store)

    async def scenario() -> None:
        created = manager.create(JobCreateRequest(video_path=str(video), target_language="en"))
        manager.run(created.id)
        await _wait_for_job(manager, created.id)

        failed = manager.get(created.id)
        assert failed.status == "failed"
        assert failed.stage == "translating"
        assert failed.steps[0].status == "succeeded"
        assert failed.steps[1].status == "succeeded"
        assert failed.steps[2].status == "failed"
        assert asr.calls == 1
        assert translator.calls == 0

        translation_config = dict(failed.steps[2].config)
        translation_config["target_language"] = "zh-CN"
        updated = manager.update_step_config(
            created.id,
            JobStep.TRANSLATION,
            translation_config,
        )
        assert updated.steps[1].status == "succeeded"
        assert updated.steps[2].status == "pending"

        manager.run_step(created.id, JobStep.TRANSLATION)
        await _wait_for_job(manager, created.id)
        completed = manager.get(created.id)
        assert completed.status == "completed"
        assert asr.calls == 1
        assert translator.calls == 1

    asyncio.run(scenario())


def test_config_changes_invalidate_only_the_affected_step_and_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR()
    translator = CountingTranslator()
    monkeypatch.setattr(
        "sublingo_local.jobs.create_translation_provider", lambda settings: translator
    )
    store, pipeline = _pipeline(tmp_path, asr=asr)
    manager = JobManager(None, pipeline, job_store=store)

    async def scenario() -> None:
        created = manager.create(JobCreateRequest(video_path=str(video)))
        manager.run(created.id)
        await _wait_for_job(manager, created.id)
        assert asr.calls == 1
        assert translator.calls == 1

        export_config = dict(manager.get(created.id).steps[3].config)
        export_config["output_directory"] = str(tmp_path / "alternate")
        export_updated = manager.update_step_config(
            created.id,
            JobStep.EXPORT,
            export_config,
        )
        assert [step.status for step in export_updated.steps] == [
            "succeeded",
            "succeeded",
            "succeeded",
            "stale",
        ]
        manager.run_step(created.id, JobStep.EXPORT)
        await _wait_for_job(manager, created.id)
        assert asr.calls == 1
        assert translator.calls == 1
        assert (tmp_path / "alternate" / "movie.srt").is_file()

        asr_config = dict(manager.get(created.id).steps[1].config)
        asr_config["beam_size"] = 2
        asr_updated = manager.update_step_config(
            created.id,
            JobStep.TRANSCRIPTION,
            asr_config,
        )
        assert [step.status for step in asr_updated.steps] == [
            "succeeded",
            "stale",
            "stale",
            "stale",
        ]
        manager.run_step(created.id, JobStep.TRANSCRIPTION)
        await _wait_for_job(manager, created.id)
        assert asr.calls == 2
        assert translator.calls == 2

    asyncio.run(scenario())


def test_job_metadata_survives_reload_and_delete_cleans_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR()
    translator = CountingTranslator()
    monkeypatch.setattr(
        "sublingo_local.jobs.create_translation_provider", lambda settings: translator
    )
    store, pipeline = _pipeline(tmp_path, asr=asr)
    manager = JobManager(None, pipeline, job_store=store)

    async def scenario() -> str:
        created = manager.create(JobCreateRequest(video_path=str(video)))
        manager.run(created.id)
        await _wait_for_job(manager, created.id)
        return created.id

    job_id = asyncio.run(scenario())
    reloaded_pipeline = ProcessingPipeline(
        store.root,
        asr_factory=lambda: asr,
        job_store=store,
    )
    reloaded = JobManager(None, reloaded_pipeline, job_store=store)
    assert reloaded.get(job_id).status == "completed"
    assert reloaded.get(job_id).steps[1].artifact is not None
    assert store.job_file(job_id).is_file()

    reloaded.delete(job_id)
    assert not store.job_file(job_id).parent.exists()
    with pytest.raises(KeyError, match="任务不存在"):
        reloaded.get(job_id)


def test_legacy_qwen_job_remains_visible_until_asr_config_is_migrated(
    tmp_path: Path,
) -> None:
    video = tmp_path / "legacy.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR()
    store, pipeline = _pipeline(tmp_path, asr=asr)
    payload = JobRecord(
        id="legacy-qwen-job",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(video),
            name=video.name,
        ),
    ).to_payload()
    payload["asr"] = {
        "provider": "qwen3_asr",
        "model": "qwen3-asr-1.7b",
        "device": "cuda",
        "compute_type": "float16",
        "vad_filter": True,
        "beam_size": 5,
        "output_mode": "word_resegmented",
    }
    store.save_job("legacy-qwen-job", payload)

    manager = JobManager(None, pipeline, job_store=store)
    view = manager.get("legacy-qwen-job")
    transcription = view.steps[1]

    assert view.asr_provider == "qwen3_asr"
    assert transcription.config["model"] == "qwen3-asr-1.7b"
    assert transcription.can_run is False
    assert "Qwen3-ASR 已停用" in (transcription.error or "")
    with pytest.raises(ValueError, match="Qwen3-ASR 已停用"):
        manager.run_step("legacy-qwen-job", JobStep.TRANSCRIPTION)

    migrated_config = dict(transcription.config)
    migrated_config.update(provider="faster_whisper", model="small")
    migrated = manager.update_step_config(
        "legacy-qwen-job",
        JobStep.TRANSCRIPTION,
        migrated_config,
    )

    assert migrated.asr_provider == "faster_whisper"
    assert migrated.steps[1].config["model"] == "small"
    assert migrated.steps[1].error is None
    assert "qwen3_asr" not in store.job_file("legacy-qwen-job").read_text(
        encoding="utf-8"
    )


def test_job_request_exposes_only_target_language() -> None:
    request = JobCreateRequest(video_path="movie.mp4")

    assert request.target_language == TargetLanguage.ZH_CN
    assert request.asr.output_mode == ASROutputMode.WORD_RESEGMENTED
    assert "source_language" not in JobCreateRequest.model_fields
    assert "output" not in JobCreateRequest.model_fields
    for value in ("zh-CN", "en", "ko"):
        assert JobCreateRequest(video_path="movie.mp4", target_language=value)


def test_runtime_api_key_is_redacted_and_never_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "failure-secret-that-must-not-persist"
    video = tmp_path / "secret-job.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR()

    class FailedTranslator(TranslationProvider):
        async def translate(  # type: ignore[no-untyped-def]
            self, items, *, source_language, target_language
        ):
            raise RuntimeError(f"provider failed with {secret}")

    def provider(settings):  # type: ignore[no-untyped-def]
        assert settings.api_key is not None
        assert settings.api_key.get_secret_value() == secret
        return FailedTranslator()

    monkeypatch.setattr("sublingo_local.jobs.create_translation_provider", provider)
    store, pipeline = _pipeline(tmp_path, asr=asr)
    manager = JobManager(None, pipeline, job_store=store)

    async def scenario() -> str:
        created = manager.create(
            JobCreateRequest(
                video_path=str(video),
                translation={"provider": "deepseek", "model": "deepseek-v4-flash"},
            )
        )
        manager.run(created.id, JobRunRequest(api_key=secret))
        await _wait_for_job(manager, created.id)
        return created.id

    job_id = asyncio.run(scenario())
    view = manager.get(job_id)
    persisted = store.job_file(job_id).read_text(encoding="utf-8")
    assert view.status == "failed"
    assert secret not in (view.error or "")
    assert "***" in (view.error or "")
    assert secret not in persisted
    assert all(secret not in log.message for log in view.logs)
