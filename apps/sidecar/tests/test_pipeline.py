import asyncio
import json
from pathlib import Path

import pytest

from sublingo_local.asr.base import ASRProvider, TranscriptionResult
from sublingo_local.asr.diagnostics import (
    ASRAudioAnalysis,
    ASRDiagnosticsSummary,
    ASRRunDiagnostics,
    ASRSegmentDiagnostics,
    ASRWindowDiagnostics,
    AudioInterval,
)
from sublingo_local.job_store import JobStore
from sublingo_local.jobs import JobManager, JobRecord, ProcessingPipeline
from sublingo_local.models import (
    ASR_HOTWORD_MAX_ENTRIES,
    ASR_HOTWORD_MAX_ENTRY_CHARACTERS,
    ASR_HOTWORD_MAX_TOTAL_CHARACTERS,
    ASROutputMode,
    ASRSettings,
    JobCreateRequest,
    JobRunRequest,
    JobStep,
    MediaStepSettings,
    ModelUsageSummary,
    SourceKind,
    StepStatus,
    SubtitleSegment,
    TargetLanguage,
    TranslatedItem,
    TranslationItem,
)
from sublingo_local.translation.base import TranslationProvider, TranslationService


def _asr_diagnostics() -> ASRRunDiagnostics:
    total_samples = 19_200
    return ASRRunDiagnostics(
        audio=ASRAudioAnalysis.unavailable(
            sample_rate=16_000,
            total_samples=total_samples,
        ),
        windows=(
            ASRWindowDiagnostics(
                index=0,
                core=AudioInterval(start_sample=0, end_sample=total_samples),
                context=AudioInterval(start_sample=0, end_sample=total_samples),
                candidate_count=1,
            ),
        ),
        segments=(
            ASRSegmentDiagnostics(
                candidate_id="candidate-chunk-000000-segment-000000",
                window_index=0,
                interval=AudioInterval(start_sample=1_600, end_sample=total_samples),
                avg_logprob=-0.1,
                word_count=1,
                valid_word_timestamp_count=1,
                word_timestamp_coverage=1.0,
            ),
        ),
        summary=ASRDiagnosticsSummary(
            window_count=1,
            candidate_segment_count=1,
            output_segment_count=1,
        ),
    )


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
            diagnostics=_asr_diagnostics(),
        )


class TokenBudgetRejectingASR(ASRProvider):
    def transcribe(self, audio_path, *, language, settings, on_progress=None):  # type: ignore[no-untyped-def]
        raise ValueError(
            "提示词经当前语音模型分词后为 549 个 Token，"
            "超过 223 个 Token 的模型上限；请减少提示词条数或缩短内容后重试"
        )


class CountingTranslator(TranslationProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def translate(  # type: ignore[no-untyped-def]
        self, items, *, source_language, target_language, on_usage=None
    ):
        self.calls += 1
        if on_usage:
            on_usage(
                ModelUsageSummary(
                    provider="test",
                    model="counting-translator",
                    request_count=1,
                    input_tokens=3,
                    output_tokens=2,
                    total_tokens=5,
                    source="provider",
                    complete=True,
                )
            )
        return [TranslatedItem(id=item.id, translated_text="你好") for item in items]


def _pipeline(
    tmp_path: Path,
    *,
    asr: ASRProvider,
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
    view = record.to_view()
    assert view.target_language == TargetLanguage.ZH_CN
    assert view.asr_provider == "faster_whisper"
    assert view.wall_duration_ms is not None and view.wall_duration_ms >= 0
    assert all(step.latest_duration_ms is not None for step in view.steps)
    assert all(step.total_duration_ms == step.latest_duration_ms for step in view.steps)
    assert view.cumulative_attempt_duration_ms == sum(
        step.total_duration_ms or 0 for step in view.steps
    )
    assert view.total_model_usage is not None
    assert view.total_model_usage.total_tokens == 5
    assert all(record.steps[step].status == StepStatus.SUCCEEDED for step in JobStep)
    assert asr.calls == 1
    assert translator.calls == 1
    assert store.artifact_path(record.id, "media.json").is_file()
    assert store.artifact_path(record.id, "transcription.json").is_file()
    assert store.artifact_path(record.id, "translation.json").is_file()
    transcription = record.steps[JobStep.TRANSCRIPTION].artifact
    assert transcription is not None
    assert transcription.summary["diagnostics"] == {
        "schema_version": 1,
        "window_strategy": "fixed",
        "window_count": 1,
        "fallback_window_count": 0,
        "boundary_shift_abs_total_samples": 0,
        "candidate_segment_count": 1,
        "deduplicated_segment_count": 0,
        "output_segment_count": 1,
        "retry_candidate_count": 0,
        "retry_request_count": 0,
        "retry_selected_count": 0,
        "retry_initial_selected_count": 0,
        "retry_failed_count": 0,
        "retry_reason_counts": {},
    }
    persisted = store.read_artifact(Path(transcription.path))
    assert persisted["diagnostics"]["audio"]["total_samples"] == 19_200


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
    reloaded_view = reloaded.get(job_id)
    assert reloaded_view.status == "completed"
    assert reloaded_view.steps[1].artifact is not None
    assert all(step.latest_duration_ms is not None for step in reloaded_view.steps)
    assert reloaded_view.cumulative_attempt_duration_ms is not None
    assert reloaded_view.total_model_usage is not None
    assert reloaded_view.total_model_usage.total_tokens == 5
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


def test_job_created_before_new_asr_features_keeps_historical_behavior(
    tmp_path: Path,
) -> None:
    payload = JobRecord(
        id="legacy-fixed-window-job",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(tmp_path / "legacy.mp4"),
            name="legacy.mp4",
        ),
    ).to_payload()
    payload["asr"].pop("dynamic_chunking")
    payload["asr"].pop("selective_retry")
    payload["asr"].pop("hotwords")

    record = JobRecord.from_payload(payload)

    assert isinstance(record.asr, ASRSettings)
    assert record.asr.dynamic_chunking is False
    assert record.asr.selective_retry is False
    assert record.asr.hotwords == []
    assert record.to_view().steps[1].config["dynamic_chunking"] is False
    assert record.to_view().steps[1].config["selective_retry"] is False
    assert record.to_view().steps[1].config["hotwords"] == []
    assert ASRSettings().dynamic_chunking is True
    assert ASRSettings().selective_retry is True


def test_asr_hotwords_normalize_trim_empty_and_stable_duplicates() -> None:
    settings = ASRSettings(
        hotwords=[
            "  CaptionNest  ",
            "",
            "初音未来",
            "CaptionNest",
            "   ",
            "葬送のフリーレン",
        ]
    )

    assert settings.hotwords == ["CaptionNest", "初音未来", "葬送のフリーレン"]
    assert ASRSettings().hotwords == []


@pytest.mark.parametrize(
    ("hotwords", "message"),
    [
        ("CaptionNest", "提示词必须是字符串数组"),
        ([123], "第 1 个提示词必须是文本"),
        (["Caption\tNest"], "不能包含换行或控制字符"),
        (
            ["x" * (ASR_HOTWORD_MAX_ENTRY_CHARACTERS + 1)],
            f"单个提示词不能超过 {ASR_HOTWORD_MAX_ENTRY_CHARACTERS} 个字符",
        ),
        (
            [f"term-{index}" for index in range(ASR_HOTWORD_MAX_ENTRIES + 1)],
            f"提示词不能超过 {ASR_HOTWORD_MAX_ENTRIES} 条",
        ),
        (
            [
                ("x" * (ASR_HOTWORD_MAX_ENTRY_CHARACTERS - 2)) + f"{index:02d}"
                for index in range(
                    ASR_HOTWORD_MAX_TOTAL_CHARACTERS
                    // ASR_HOTWORD_MAX_ENTRY_CHARACTERS
                    + 1
                )
            ],
            f"提示词总字符数不能超过 {ASR_HOTWORD_MAX_TOTAL_CHARACTERS} 个",
        ),
    ],
)
def test_asr_hotwords_reject_invalid_or_oversized_values(
    hotwords: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ASRSettings(hotwords=hotwords)  # type: ignore[arg-type]


def test_transcription_logs_and_diagnostics_only_expose_hotword_count(
    tmp_path: Path,
) -> None:
    private_hotword = "DO-NOT-LEAK-HOTWORD"
    video = tmp_path / "hotword.mp4"
    video.write_bytes(b"fake video")
    asr = CountingASR()
    store, pipeline = _pipeline(tmp_path, asr=asr)
    record = JobRecord(
        id="hotword-job",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(video),
            name=video.name,
        ),
        asr=ASRSettings(hotwords=[private_hotword, "初音未来"]),
    )

    asyncio.run(pipeline.run_from(record, JobStep.MEDIA, continue_pipeline=False))
    asyncio.run(
        pipeline.run_from(record, JobStep.TRANSCRIPTION, continue_pipeline=False)
    )

    log_payload = json.dumps(
        [item.model_dump(mode="json") for item in record.logs],
        ensure_ascii=False,
    )
    artifact = record.steps[JobStep.TRANSCRIPTION].artifact
    assert artifact is not None
    persisted_diagnostics = store.read_artifact(Path(artifact.path))
    assert private_hotword not in log_payload
    assert private_hotword not in json.dumps(persisted_diagnostics, ensure_ascii=False)
    assert any("已配置 2 个提示词" in item.message for item in record.logs)
    assert any("已使用 2 个提示词" in item.message for item in record.logs)
    assert artifact.summary["hotword_count"] == 2


def test_hotword_token_budget_failure_log_does_not_expose_content(
    tmp_path: Path,
) -> None:
    private_hotword = "DO-NOT-LEAK-HOTWORD"
    video = tmp_path / "hotword-over-budget.mp4"
    video.write_bytes(b"fake video")
    store, pipeline = _pipeline(tmp_path, asr=TokenBudgetRejectingASR())
    manager = JobManager(None, pipeline, job_store=store)

    async def scenario() -> str:
        created = manager.create(
            JobCreateRequest(
                video_path=str(video),
                asr={"hotwords": [private_hotword, "初音未来"]},
            )
        )
        manager.run(created.id)
        await _wait_for_job(manager, created.id)
        return created.id

    job_id = asyncio.run(scenario())
    view = manager.get(job_id)
    log_payload = json.dumps(
        [item.model_dump(mode="json") for item in view.logs],
        ensure_ascii=False,
    )

    assert view.status == "failed"
    assert view.steps[1].status == "failed"
    assert private_hotword not in log_payload
    assert "已配置 2 个提示词" in log_payload
    assert "已使用 2 个提示词" not in log_payload
    assert "超过 223 个 Token 的模型上限" in (view.error or "")


def test_job_request_exposes_only_target_language() -> None:
    request = JobCreateRequest(video_path="movie.mp4")

    assert request.target_language == TargetLanguage.ZH_CN
    assert request.asr.output_mode == ASROutputMode.WORD_RESEGMENTED
    assert request.asr.selective_retry is True
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
            self, items, *, source_language, target_language, on_usage=None
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


def test_failed_cancelled_and_retried_attempts_keep_monotonic_durations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((10.0, 10.125, 20.0, 20.25))
    monkeypatch.setattr("sublingo_local.jobs.time.perf_counter", lambda: next(ticks))
    record = JobRecord(
        id="timed-job",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(tmp_path / "movie.mp4"),
            name="movie.mp4",
        ),
    )

    record.begin_step(JobStep.MEDIA, "第一次执行")
    record.fail_current(RuntimeError("第一次失败"))
    record.begin_step(JobStep.MEDIA, "第二次执行")
    record.cancel_current()

    view = record.to_view()
    media = view.steps[0]
    assert [attempt.duration_ms for attempt in media.attempts] == [125, 250]
    assert [attempt.status for attempt in media.attempts] == ["failed", "cancelled"]
    assert all(attempt.finished_at is not None for attempt in media.attempts)
    assert media.latest_duration_ms == 250
    assert media.total_duration_ms == 375
    assert view.cumulative_attempt_duration_ms == 375


def test_job_payload_without_metrics_remains_loadable(tmp_path: Path) -> None:
    record = JobRecord(
        id="legacy-metrics-job",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(tmp_path / "movie.mp4"),
            name="movie.mp4",
        ),
    )
    record.begin_step(JobStep.MEDIA, "旧版本执行")
    record.cancel_current()
    payload = record.to_payload()
    attempt = payload["steps"][0]["attempts"][0]
    attempt.pop("duration_ms")
    attempt.pop("model_usage")
    payload["schema_version"] = 1

    restored = JobRecord.from_payload(payload).to_view()

    assert restored.steps[0].attempts[0].duration_ms is None
    assert restored.steps[0].attempts[0].model_usage is None
    assert restored.steps[0].total_duration_ms is None
    assert restored.cumulative_attempt_duration_ms is None


def test_partial_translation_usage_is_persisted_before_later_chunk_failure(
    tmp_path: Path,
) -> None:
    snapshots: list[dict[str, object]] = []
    record = JobRecord(
        id="partial-usage-job",
        media=MediaStepSettings(
            source_kind=SourceKind.PATH,
            path=str(tmp_path / "movie.mp4"),
            name="movie.mp4",
        ),
    )
    record.attach_persistence(lambda payload: snapshots.append(dict(payload)))

    class PartialFailureTranslator(TranslationProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def translate(  # type: ignore[no-untyped-def]
            self, items, *, source_language, target_language, on_usage=None
        ):
            self.calls += 1
            if on_usage:
                on_usage(
                    ModelUsageSummary(
                        provider="deepseek",
                        model="partial-model",
                        request_count=1,
                        input_tokens=4,
                        output_tokens=2,
                        total_tokens=6,
                        source="provider",
                        complete=True,
                    )
                )
            if self.calls == 2:
                raise RuntimeError("second chunk failed")
            return [
                TranslatedItem(id=item.id, translated_text="译文") for item in items
            ]

    record.begin_step(JobStep.TRANSLATION, "开始分片翻译")
    service = TranslationService(PartialFailureTranslator(), max_items_per_chunk=1)
    with pytest.raises(RuntimeError, match="second chunk failed"):
        asyncio.run(
            service.translate(
                [
                    TranslationItem(id="1", text="first"),
                    TranslationItem(id="2", text="second"),
                ],
                source_language="en",
                target_language=TargetLanguage.ZH_CN,
                on_usage=lambda usage: record.record_model_usage(
                    JobStep.TRANSLATION,
                    usage,
                ),
            )
        )

    # The second callback is persisted while the Attempt is still running.
    running_attempt = snapshots[-1]["steps"][2]["attempts"][0]
    assert running_attempt["status"] == "running"
    assert running_attempt["model_usage"]["request_count"] == 2
    assert running_attempt["model_usage"]["total_tokens"] == 12

    record.fail_current(RuntimeError("second chunk failed"))
    failed_attempt = record.to_view().steps[2].attempts[0]
    assert failed_attempt.status == "failed"
    assert failed_attempt.duration_ms is not None
    assert failed_attempt.model_usage is not None
    assert failed_attempt.model_usage.request_count == 2
    assert failed_attempt.model_usage.total_tokens == 12
