import asyncio
from pathlib import Path

import pytest

from sublingo_local.asr.base import ASRProvider, TranscriptionResult
from sublingo_local.jobs import JobRecord, ProcessingPipeline
from sublingo_local.models import (
    JobCreateRequest,
    ResolvedSource,
    SourceKind,
    SubtitleSegment,
    TranslatedItem,
)
from sublingo_local.translation.base import TranslationProvider


class FakeASR(ASRProvider):
    def transcribe(self, audio_path, *, language, settings, on_progress=None):  # type: ignore[no-untyped-def]
        assert audio_path.exists()
        if on_progress:
            on_progress(1.0)
        return TranscriptionResult(
            language="ja",
            segments=[
                SubtitleSegment(
                    id="seg-000001", start_ms=100, end_ms=1_200, text="こんにちは"
                )
            ],
        )


class FakeTranslator(TranslationProvider):
    async def translate(self, items, *, source_language):  # type: ignore[no-untyped-def]
        assert source_language == "ja"
        return [TranslatedItem(id=item.id, translated_text="你好") for item in items]


def test_path_pipeline_writes_both_subtitles_next_to_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"fake video")

    def fake_extract(video_path: Path, audio_path: Path) -> None:
        assert video_path == video
        audio_path.write_bytes(b"fake wav")

    monkeypatch.setattr("sublingo_local.jobs.extract_audio", fake_extract)
    monkeypatch.setattr(
        "sublingo_local.jobs.create_translation_provider", lambda settings: FakeTranslator()
    )
    request = JobCreateRequest(
        video_path=str(video),
        source_language="ja",
        translation={"provider": "codex_spark"},
    )
    record = JobRecord(
        id="job-1",
        request=request,
        source=ResolvedSource(kind=SourceKind.PATH, path=video, name=video.name),
    )
    pipeline = ProcessingPipeline(tmp_path / "temp", asr_factory=FakeASR)

    asyncio.run(pipeline.run(record))

    assert record.source_subtitle_path == str(tmp_path / "movie.ja.srt")
    assert record.translated_subtitle_path == str(tmp_path / "movie.zh-CN.srt")
    assert (tmp_path / "movie.ja.srt").exists()
    assert "你好" in (tmp_path / "movie.zh-CN.srt").read_text(encoding="utf-8-sig")

