from __future__ import annotations

import gc
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..model_manager import ModelManager
from ..models import ASRSettings, SubtitleSegment
from .base import ASRProgress, ASRProvider, TranscriptionResult

_SAMPLE_RATE = 16_000
_MAX_ALIGNMENT_CHUNK_SECONDS = 60.0
_MAX_NEW_TOKENS = 1_024
_MIN_CUE_DURATION_MS = 400
_TERMINAL_PUNCTUATION = ("。", "！", "？", "!", "?", ".", "…")
_NO_SPACE_BEFORE = set("，。！？；：、,.!?;:%)]}〉》」』】’\"")
_NO_SPACE_AFTER = set("([{〈《「『【‘\"")
_CJK_LANGUAGES = {
    "chinese",
    "cantonese",
    "japanese",
    "korean",
    "zh",
    "yue",
    "ja",
    "ko",
}
_QWEN_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "cmn": "Chinese",
    "yue": "Cantonese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "es": "Spanish",
}
_LANGUAGE_TO_ISO = {
    "chinese": "zh",
    "cantonese": "yue",
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
}


@dataclass(frozen=True)
class _AlignedItem:
    text: str
    start_time: float
    end_time: float


def _normalized_language(language: str) -> str:
    normalized = language.strip().lower().replace("_", "-")
    return _LANGUAGE_TO_ISO.get(normalized, normalized)


def _qwen_language(language: str) -> str | None:
    normalized = language.strip().lower().replace("_", "-")
    if normalized == "auto":
        return None
    return _QWEN_LANGUAGE_NAMES.get(normalized, language.strip())


def _join_aligned_text(current: str, token: str, *, cjk: bool) -> str:
    token = re.sub(r"\s+", " ", token.strip())
    if not current or not token:
        return current + token
    if cjk or token[0] in _NO_SPACE_BEFORE or current[-1] in _NO_SPACE_AFTER:
        return current + token
    return f"{current} {token}"


def _non_lexical_text(value: str) -> str:
    return "".join(character for character in value if not character.isalnum())


def _aligned_items_with_punctuation(
    items: Iterable[object],
    *,
    transcript: str,
    offset_seconds: float = 0.0,
) -> list[_AlignedItem]:
    """Keep zero-length aligned tokens and restore punctuation stripped by the aligner."""

    aligned: list[_AlignedItem] = []
    cursor = 0
    for raw_item in items:
        text = str(getattr(raw_item, "text", "")).strip()
        try:
            start_time = max(0.0, float(getattr(raw_item, "start_time", None)))
            end_time = float(getattr(raw_item, "end_time", None))
        except (TypeError, ValueError):
            continue
        if not text or text.startswith("<|") or end_time < start_time:
            continue

        position = transcript.find(text, cursor)
        if position >= 0:
            between = _non_lexical_text(transcript[cursor:position])
            if between and aligned:
                previous = aligned[-1]
                aligned[-1] = _AlignedItem(
                    text=previous.text + between,
                    start_time=previous.start_time,
                    end_time=previous.end_time,
                )
            elif between:
                text = between + text
            cursor = position + len(str(getattr(raw_item, "text", "")).strip())

        aligned.append(
            _AlignedItem(
                text=text,
                start_time=start_time + offset_seconds,
                end_time=end_time + offset_seconds,
            )
        )

    suffix = _non_lexical_text(transcript[cursor:])
    if suffix and aligned:
        previous = aligned[-1]
        aligned[-1] = _AlignedItem(
            text=previous.text + suffix,
            start_time=previous.start_time,
            end_time=previous.end_time,
        )
    return aligned


def _split_for_alignment(audio: Any) -> list[tuple[Any, float]]:
    # qwen-asr is pinned because its public transcribe API does not expose this limit.
    # Shorter low-energy windows prevent one failed forced alignment from drifting
    # across the package default of 180 seconds.
    from qwen_asr.inference.utils import split_audio_into_chunks

    return split_audio_into_chunks(
        audio,
        _SAMPLE_RATE,
        max_chunk_sec=_MAX_ALIGNMENT_CHUNK_SECONDS,
    )


def _alignment_quality_issue(items: Iterable[object]) -> str | None:
    """Detect collapsed forced-alignment output before it becomes misleading SRT."""

    total_characters = 0
    zero_duration_characters = 0
    zero_run_characters = 0
    longest_zero_run = 0
    for item in items:
        text = re.sub(r"\s+", "", str(getattr(item, "text", "")))
        if not text:
            continue
        try:
            start_time = float(getattr(item, "start_time", None))
            end_time = float(getattr(item, "end_time", None))
        except (TypeError, ValueError):
            continue
        characters = len(text)
        total_characters += characters
        duration = end_time - start_time
        if duration <= 0:
            zero_duration_characters += characters
            zero_run_characters += characters
            longest_zero_run = max(longest_zero_run, zero_run_characters)
        else:
            zero_run_characters = 0
            if duration > 15 and characters <= 6:
                return f"单个词跨越 {duration:.1f} 秒"

    if total_characters < 12:
        return None
    if longest_zero_run >= 12:
        return f"连续 {longest_zero_run} 个字没有有效时长"
    zero_ratio = zero_duration_characters / total_characters
    if zero_ratio >= 0.35:
        return f"{zero_ratio:.0%} 文字没有有效时长"
    return None


def aligned_items_to_segments(
    items: Iterable[object],
    *,
    language: str,
    max_duration_seconds: float = 7.0,
    max_gap_seconds: float = 0.9,
) -> list[SubtitleSegment]:
    """Group Qwen word/character timestamps into readable subtitle cues."""

    normalized_language = language.strip().lower().replace("_", "-")
    cjk = normalized_language in _CJK_LANGUAGES
    max_characters = 28 if cjk else 52
    aligned: list[_AlignedItem] = []
    for raw_item in items:
        text = str(getattr(raw_item, "text", "")).strip()
        try:
            start_time = max(0.0, float(getattr(raw_item, "start_time", None)))
            end_time = float(getattr(raw_item, "end_time", None))
        except (TypeError, ValueError):
            continue
        if not text or text.startswith("<|") or end_time < start_time:
            continue
        aligned.append(_AlignedItem(text=text, start_time=start_time, end_time=end_time))

    segments: list[SubtitleSegment] = []
    current_text = ""
    current_start = 0.0
    current_end = 0.0

    def flush() -> None:
        nonlocal current_text, current_start, current_end
        text = current_text.strip()
        if text:
            start_ms = max(0, round(current_start * 1_000))
            end_ms = max(start_ms + 1, round(current_end * 1_000))
            segments.append(
                SubtitleSegment(
                    id=f"seg-{len(segments) + 1:06d}",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                )
            )
        current_text = ""
        current_start = 0.0
        current_end = 0.0

    for item in aligned:
        proposed_text = _join_aligned_text(current_text, item.text, cjk=cjk)
        if current_text:
            gap = max(0.0, item.start_time - current_end)
            proposed_duration = item.end_time - current_start
            exceeds_readable_size = len(proposed_text) > max_characters
            exceeds_duration = proposed_duration > max_duration_seconds
            if gap >= max_gap_seconds or exceeds_readable_size or exceeds_duration:
                flush()
                proposed_text = _join_aligned_text("", item.text, cjk=cjk)

        if not current_text:
            current_start = item.start_time
        current_text = proposed_text
        current_end = max(current_end, item.end_time)

        cue_duration = current_end - current_start
        if current_text.endswith(_TERMINAL_PUNCTUATION) and cue_duration >= 0.45:
            flush()

    flush()
    readable_segments: list[SubtitleSegment] = []
    for index, segment in enumerate(segments):
        desired_end = segment.start_ms + _MIN_CUE_DURATION_MS
        if index + 1 < len(segments) and segments[index + 1].start_ms > segment.end_ms:
            desired_end = min(desired_end, segments[index + 1].start_ms - 1)
        readable_segments.append(
            segment.model_copy(update={"end_ms": max(segment.end_ms, desired_end)})
        )
    return readable_segments


class Qwen3ASRProvider(ASRProvider):
    """Qwen3-ASR 1.7B adapter with local forced alignment and lazy imports."""

    def __init__(self, model_manager: ModelManager) -> None:
        self.model_manager = model_manager

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str,
        settings: ASRSettings,
        on_progress: ASRProgress | None = None,
    ) -> TranscriptionResult:
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:
            raise RuntimeError(
                "尚未安装 Qwen3-ASR 运行时，请执行 uv sync --extra qwen"
            ) from exc

        components = self.model_manager.resolve_installed_components(settings.model)
        try:
            asr_path = components["asr"]
            aligner_path = components["aligner"]
        except KeyError as exc:
            raise RuntimeError("Qwen3-ASR 模型包缺少 ASR 或时间对齐组件") from exc

        if settings.device == "auto":
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        elif settings.device == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("Qwen3-ASR 已选择 CUDA，但 PyTorch 未检测到可用显卡")
            device = "cuda:0"
        else:
            device = "cpu"
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32

        model: Any | None = None
        audio: Any | None = None
        try:
            audio, duration = self._decode_to_mono_16k(audio_path, on_progress=on_progress)
            if on_progress:
                on_progress(0.1)
            model = Qwen3ASRModel.from_pretrained(
                str(asr_path),
                dtype=dtype,
                device_map=device,
                max_inference_batch_size=1,
                max_new_tokens=_MAX_NEW_TOKENS,
                forced_aligner=str(aligner_path),
                forced_aligner_kwargs={"dtype": dtype, "device_map": device},
            )
            chunks = _split_for_alignment(audio)
            if not chunks:
                raise RuntimeError("音频分块失败，无法执行 Qwen3-ASR")
            requested_language = _qwen_language(language)
            inferred_language: str | None = None
            detected_languages: list[str] = []
            segments: list[SubtitleSegment] = []
            for index, (chunk, offset_seconds) in enumerate(chunks, start=1):
                language_for_chunk = requested_language or inferred_language
                results = model.transcribe(
                    audio=(chunk, _SAMPLE_RATE),
                    language=language_for_chunk,
                    return_time_stamps=True,
                )
                if results:
                    result = results[0]
                    raw_chunk_language = str(getattr(result, "language", "") or "").strip()
                    if raw_chunk_language.lower() not in {"", "auto", "unknown"}:
                        detected_languages.append(raw_chunk_language)
                        if inferred_language is None:
                            inferred_language = raw_chunk_language
                    chunk_language = (
                        raw_chunk_language
                        or (detected_languages[-1] if detected_languages else "")
                        or requested_language
                        or language
                    )
                    timestamps = getattr(result, "time_stamps", None)
                    timestamp_items = getattr(timestamps, "items", None)
                    if timestamp_items is not None:
                        timestamp_items = list(timestamp_items)
                        quality_issue = _alignment_quality_issue(timestamp_items)
                        if quality_issue:
                            raise RuntimeError(
                                "Qwen3 ForcedAligner 时间戳质量校验失败："
                                f"{offset_seconds:.1f} 秒附近{quality_issue}。"
                                "该素材不适合直接使用 Qwen 时间对齐。"
                            )
                        aligned_items = _aligned_items_with_punctuation(
                            timestamp_items,
                            transcript=str(getattr(result, "text", "") or ""),
                            offset_seconds=offset_seconds,
                        )
                        chunk_segments = aligned_items_to_segments(
                            aligned_items,
                            language=chunk_language,
                        )
                        for segment in chunk_segments:
                            segments.append(
                                segment.model_copy(
                                    update={"id": f"seg-{len(segments) + 1:06d}"}
                                )
                            )
                if on_progress:
                    on_progress(0.1 + index / len(chunks) * 0.9)
            if not detected_languages:
                raise RuntimeError("Qwen3-ASR 没有返回识别结果")
            detected_language = Counter(detected_languages).most_common(1)[0][0]
            if not segments:
                raise RuntimeError("没有从音频中识别出有效语音")
            return TranscriptionResult(
                language=_normalized_language(detected_language),
                duration_seconds=duration,
                segments=segments,
            )
        finally:
            audio = None
            model = None
            gc.collect()
            if device.startswith("cuda"):
                torch.cuda.empty_cache()

    @staticmethod
    def _decode_to_mono_16k(
        path: Path,
        *,
        on_progress: ASRProgress | None,
    ) -> tuple[Any, float]:
        try:
            import av
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("媒体解码组件不可用，请重新安装应用") from exc

        chunks: list[Any] = []
        decoded_samples = 0
        last_reported = -1.0
        with av.open(str(path)) as container:
            stream = next(iter(container.streams.audio), None)
            if stream is None:
                raise RuntimeError("视频中没有可识别的音轨")
            duration_seconds = (
                float(stream.duration * stream.time_base)
                if stream.duration is not None and stream.time_base is not None
                else 0.0
            )
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=_SAMPLE_RATE)
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    samples = np.asarray(converted.to_ndarray(), dtype=np.float32).reshape(-1)
                    if samples.size:
                        chunks.append(samples.copy())
                        decoded_samples += int(samples.size)
                if on_progress and duration_seconds > 0:
                    progress = min(0.08, decoded_samples / (_SAMPLE_RATE * duration_seconds) * 0.08)
                    if progress - last_reported >= 0.005:
                        on_progress(progress)
                        last_reported = progress
            for converted in resampler.resample(None):
                samples = np.asarray(converted.to_ndarray(), dtype=np.float32).reshape(-1)
                if samples.size:
                    chunks.append(samples.copy())
                    decoded_samples += int(samples.size)

        if not chunks or decoded_samples == 0:
            raise RuntimeError("视频音轨为空，无法识别")
        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        return audio, decoded_samples / _SAMPLE_RATE
