from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from .models import ASRProviderName

ModelState = Literal["ready", "missing", "downloading", "damaged"]

_WHISPER_ALLOW_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
)
_WHISPER_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json")
_QWEN_ALLOW_PATTERNS = ("*.json", "*.txt", "*.safetensors")


@dataclass(frozen=True)
class ModelArtifactSpec:
    name: str
    repo_id: str
    revision: str
    allow_patterns: tuple[str, ...]
    required_files: tuple[str, ...]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    repo_id: str
    recommended_for: Literal["cpu", "cuda", "quality"]
    revision: str
    provider: ASRProviderName = ASRProviderName.FASTER_WHISPER
    artifacts: tuple[ModelArtifactSpec, ...] = ()


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="small",
        label="small · CPU 轻量",
        repo_id="Systran/faster-whisper-small",
        recommended_for="cpu",
        revision="536b0662742c02347bc0e980a01041f333bce120",
    ),
    ModelSpec(
        id="medium",
        label="medium · CPU 均衡",
        repo_id="Systran/faster-whisper-medium",
        recommended_for="cpu",
        revision="08e178d48790749d25932bbc082711ddcfdfbc4f",
    ),
    ModelSpec(
        id="large-v3-turbo",
        label="large-v3-turbo · GPU 速度优先",
        repo_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        recommended_for="cuda",
        revision="0c94664816ec82be77b20e824c8e8675995b0029",
    ),
    ModelSpec(
        id="large-v3",
        label="large-v3 · 精度优先",
        repo_id="Systran/faster-whisper-large-v3",
        recommended_for="quality",
        revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
    ),
    ModelSpec(
        id="qwen3-asr-1.7b",
        label="Qwen3-ASR-1.7B + ForcedAligner · 实验兼容",
        repo_id="Qwen/Qwen3-ASR-1.7B",
        recommended_for="quality",
        revision="7278e1e70fe206f11671096ffdd38061171dd6e5",
        provider=ASRProviderName.QWEN3_ASR,
        artifacts=(
            ModelArtifactSpec(
                name="asr",
                repo_id="Qwen/Qwen3-ASR-1.7B",
                revision="7278e1e70fe206f11671096ffdd38061171dd6e5",
                allow_patterns=_QWEN_ALLOW_PATTERNS,
                required_files=(
                    "config.json",
                    "model-00001-of-00002.safetensors",
                    "model-00002-of-00002.safetensors",
                    "model.safetensors.index.json",
                    "preprocessor_config.json",
                    "tokenizer_config.json",
                    "merges.txt",
                    "vocab.json",
                ),
            ),
            ModelArtifactSpec(
                name="aligner",
                repo_id="Qwen/Qwen3-ForcedAligner-0.6B",
                revision="c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
                allow_patterns=_QWEN_ALLOW_PATTERNS,
                required_files=(
                    "config.json",
                    "model.safetensors",
                    "preprocessor_config.json",
                    "tokenizer_config.json",
                    "merges.txt",
                    "vocab.json",
                ),
            ),
        ),
    ),
)

_MANIFEST_FILENAME = ".captionnest-model-manifest.json"
_LEGACY_MANIFEST_FILENAME = ".sublingo-model-manifest.json"
_MANIFEST_VERSION = 1
_BUNDLE_MANIFEST_VERSION = 2
_HASH_CHUNK_SIZE = 1024 * 1024


class ModelView(BaseModel):
    id: str
    label: str
    provider: ASRProviderName = ASRProviderName.FASTER_WHISPER
    status: ModelState
    path: str | None = None
    message: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    recommended_for: Literal["cpu", "cuda", "quality"]


class ModelListView(BaseModel):
    items: list[ModelView]
    model_root: str


@dataclass
class _DownloadState:
    status: ModelState
    expected_bytes: int | None = None
    temporary_path: Path | None = None
    error: str | None = None


class ModelManager:
    """Own app-managed ASR models without importing heavyweight runtimes at startup."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._downloads_root = self.root / ".downloads"
        self._downloads_root.mkdir(parents=True, exist_ok=True)
        self._specs = {spec.id: spec for spec in MODEL_SPECS}
        self._states: dict[str, _DownloadState] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def model_path(self, model_id: str) -> Path:
        self._require_spec(model_id)
        path = (self.root / model_id).resolve()
        if self.root not in path.parents:
            raise ValueError("模型路径越界")
        return path

    def resolve_installed_path(self, model_id: str) -> Path:
        spec = self._require_spec(model_id)
        path = self.model_path(model_id)
        if not self._is_valid_model(path, spec):
            raise RuntimeError(f"尚未下载识别模型 {model_id}，请先在环境面板中下载")
        return path

    def resolve_installed_components(self, model_id: str) -> dict[str, Path]:
        spec = self._require_spec(model_id)
        root = self.resolve_installed_path(model_id)
        if not spec.artifacts:
            return {"model": root}
        return {artifact.name: root / artifact.name for artifact in spec.artifacts}

    def list(self) -> ModelListView:
        return ModelListView(
            items=[self.get(spec.id) for spec in MODEL_SPECS], model_root=str(self.root)
        )

    def get(self, model_id: str) -> ModelView:
        spec = self._require_spec(model_id)
        path = self.model_path(model_id)
        with self._lock:
            thread = self._threads.get(model_id)
            state = self._states.get(model_id)
            downloading = bool(thread and thread.is_alive()) or bool(
                state and state.status == "downloading"
            )

            if downloading and state:
                return ModelView(
                    id=spec.id,
                    label=spec.label,
                    provider=spec.provider,
                    status="downloading",
                    path=None,
                    message="正在下载模型，进度会自动更新",
                    progress=self._download_progress(state),
                    recommended_for=spec.recommended_for,
                )

            if state and state.error:
                if self._is_valid_model(path, spec):
                    return ModelView(
                        id=spec.id,
                        label=spec.label,
                        provider=spec.provider,
                        status="ready",
                        path=str(path),
                        message=f"模型更新失败，继续使用已有版本：{state.error}",
                        progress=100,
                        recommended_for=spec.recommended_for,
                    )
                status: ModelState = "damaged" if path.exists() else "missing"
                return ModelView(
                    id=spec.id,
                    label=spec.label,
                    provider=spec.provider,
                    status=status,
                    path=str(path) if path.exists() else None,
                    message=state.error,
                    recommended_for=spec.recommended_for,
                )

        if self._is_valid_model(path, spec):
            return ModelView(
                id=spec.id,
                label=spec.label,
                provider=spec.provider,
                status="ready",
                path=str(path),
                message=None,
                progress=100,
                recommended_for=spec.recommended_for,
            )
        if path.exists():
            return ModelView(
                id=spec.id,
                label=spec.label,
                provider=spec.provider,
                status="damaged",
                path=str(path),
                message="模型文件不完整，请重新下载",
                recommended_for=spec.recommended_for,
            )
        return ModelView(
            id=spec.id,
            label=spec.label,
            provider=spec.provider,
            status="missing",
            path=None,
            message="尚未下载",
            recommended_for=spec.recommended_for,
        )

    def start_download(self, model_id: str) -> ModelView:
        self._require_spec(model_id)
        with self._lock:
            existing = self._threads.get(model_id)
            if existing and existing.is_alive():
                return self.get(model_id)
            state = _DownloadState(status="downloading")
            self._states[model_id] = state
            thread = threading.Thread(
                target=self._download,
                args=(model_id, state),
                name=f"captionnest-model-{model_id}",
                daemon=True,
            )
            self._threads[model_id] = thread
            thread.start()
        return self.get(model_id)

    def _download(self, model_id: str, state: _DownloadState) -> None:
        spec = self._require_spec(model_id)
        destination = self.model_path(model_id)
        temporary = self._downloads_root / f"{model_id}-{spec.revision[:12]}-partial"
        state.temporary_path = temporary
        backup: Path | None = None
        cleanup_partial = False
        try:
            configured_endpoint = os.getenv("CAPTIONNEST_HF_ENDPOINT", "").strip()
            if configured_endpoint:
                os.environ["HF_ENDPOINT"] = configured_endpoint
            elif os.getenv("HF_ENDPOINT", "").rstrip("/") == "https://hf-mirror.com":
                os.environ["HF_ENDPOINT"] = "https://huggingface.co"
            from huggingface_hub import HfApi, snapshot_download

            api = HfApi()
            if spec.artifacts:
                infos = {
                    artifact.name: api.model_info(
                        artifact.repo_id,
                        revision=artifact.revision,
                        files_metadata=True,
                    )
                    for artifact in spec.artifacts
                }
                manifest = self._build_bundle_manifest(spec, infos)
                state.expected_bytes = sum(
                    int(metadata["size"])
                    for artifact_manifest in manifest["artifacts"].values()
                    for metadata in artifact_manifest["files"].values()
                )
            else:
                info = api.model_info(spec.repo_id, revision=spec.revision, files_metadata=True)
                manifest = self._build_manifest(spec, info)
                state.expected_bytes = sum(
                    int(metadata["size"]) for metadata in manifest["files"].values()
                )
            if state.expected_bytes:
                required = round(state.expected_bytes * 1.15)
                if shutil.disk_usage(self.root).free < required:
                    raise RuntimeError("磁盘空间不足，无法下载所选识别模型")

            # Keep this revision-scoped staging directory after transport failures so
            # Hugging Face Hub can resume its .incomplete files on the next attempt.
            temporary.mkdir(parents=True, exist_ok=True)
            if spec.artifacts:
                for artifact in spec.artifacts:
                    snapshot_download(
                        repo_id=artifact.repo_id,
                        revision=artifact.revision,
                        local_dir=temporary / artifact.name,
                        allow_patterns=list(artifact.allow_patterns),
                    )
                try:
                    self._verify_bundle_model(temporary, spec, manifest, verify_hashes=True)
                except RuntimeError:
                    cleanup_partial = not self._bundle_download_is_incomplete(
                        temporary, spec, manifest
                    )
                    raise
                cleanup_partial = True
            else:
                snapshot_download(
                    repo_id=spec.repo_id,
                    revision=spec.revision,
                    local_dir=temporary,
                    allow_patterns=list(_WHISPER_ALLOW_PATTERNS),
                )
                try:
                    self._verify_model_files(temporary, spec, manifest, verify_hashes=True)
                except RuntimeError:
                    cleanup_partial = not self._download_is_incomplete(temporary, manifest)
                    raise
                cleanup_partial = True
            self._write_manifest(temporary, manifest)

            if destination.exists():
                backup = self._downloads_root / f"{model_id}-backup-{uuid.uuid4().hex}"
                destination.replace(backup)
            temporary.replace(destination)
            if backup:
                shutil.rmtree(backup, ignore_errors=True)
            with self._lock:
                state.status = "ready"
                state.error = None
                state.temporary_path = None
        except Exception as exc:
            if backup and backup.exists() and not destination.exists():
                backup.replace(destination)
            with self._lock:
                state.status = (
                    "ready"
                    if self._is_valid_model(destination, spec)
                    else ("damaged" if destination.exists() else "missing")
                )
                state.error = self._safe_error(exc)
        finally:
            if cleanup_partial and temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)

    def _download_progress(self, state: _DownloadState) -> int | None:
        if not state.expected_bytes or not state.temporary_path:
            return None
        downloaded = 0
        try:
            for item in state.temporary_path.rglob("*"):
                if item.is_file():
                    downloaded += item.stat().st_size
        except OSError:
            return None
        return max(0, min(99, round(downloaded / state.expected_bytes * 100)))

    def _require_spec(self, model_id: str) -> ModelSpec:
        try:
            return self._specs[model_id]
        except KeyError as exc:
            raise ValueError(f"不支持的识别模型：{model_id}") from exc

    @classmethod
    def _build_manifest(cls, spec: ModelSpec, info: object) -> dict[str, object]:
        resolved_revision = str(getattr(info, "sha", "") or "")
        if resolved_revision != spec.revision:
            raise RuntimeError("模型仓库返回的提交与固定版本不一致")

        files: dict[str, dict[str, object]] = {}
        for sibling in getattr(info, "siblings", ()) or ():
            raw_name = getattr(sibling, "rfilename", None)
            if not isinstance(raw_name, str) or not any(
                fnmatch.fnmatch(raw_name, pattern) for pattern in _WHISPER_ALLOW_PATTERNS
            ):
                continue
            name = cls._validated_relative_name(raw_name)
            if name in files:
                raise RuntimeError(f"模型元数据包含重复文件：{name}")

            declared_size = getattr(sibling, "size", None)
            lfs = getattr(sibling, "lfs", None)
            lfs_size = getattr(lfs, "size", None) if lfs is not None else None
            if (
                declared_size is not None
                and lfs_size is not None
                and int(declared_size) != int(lfs_size)
            ):
                raise RuntimeError(f"模型元数据文件大小不一致：{name}")
            size_value = lfs_size if lfs_size is not None else declared_size
            try:
                size = int(size_value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"模型元数据缺少文件大小：{name}") from exc
            if size <= 0:
                raise RuntimeError(f"模型元数据文件大小无效：{name}")

            file_metadata: dict[str, object] = {"size": size}
            raw_sha256 = getattr(lfs, "sha256", None) if lfs is not None else None
            if raw_sha256:
                sha256 = str(raw_sha256).strip().lower()
                if len(sha256) != 64 or any(
                    character not in "0123456789abcdef" for character in sha256
                ):
                    raise RuntimeError(f"模型元数据 SHA-256 无效：{name}")
                file_metadata["sha256"] = sha256
            files[name] = file_metadata

        missing = sorted(set(_WHISPER_REQUIRED_FILES) - set(files))
        if missing:
            raise RuntimeError(f"模型元数据缺少必需文件：{', '.join(missing)}")
        return {
            "manifest_version": _MANIFEST_VERSION,
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "files": files,
        }

    @classmethod
    def _build_bundle_manifest(
        cls,
        spec: ModelSpec,
        infos: dict[str, object],
    ) -> dict[str, object]:
        artifacts: dict[str, dict[str, object]] = {}
        for artifact in spec.artifacts:
            info = infos.get(artifact.name)
            if info is None:
                raise RuntimeError(f"模型元数据缺少组件：{artifact.name}")
            resolved_revision = str(getattr(info, "sha", "") or "")
            if resolved_revision != artifact.revision:
                raise RuntimeError(f"模型组件 {artifact.name} 的提交与固定版本不一致")
            files = cls._collect_manifest_files(
                info,
                allow_patterns=artifact.allow_patterns,
                required_files=artifact.required_files,
            )
            artifacts[artifact.name] = {
                "repo_id": artifact.repo_id,
                "revision": artifact.revision,
                "files": files,
            }
        return {
            "manifest_version": _BUNDLE_MANIFEST_VERSION,
            "model_id": spec.id,
            "artifacts": artifacts,
        }

    @classmethod
    def _collect_manifest_files(
        cls,
        info: object,
        *,
        allow_patterns: tuple[str, ...],
        required_files: tuple[str, ...],
    ) -> dict[str, dict[str, object]]:
        files: dict[str, dict[str, object]] = {}
        for sibling in getattr(info, "siblings", ()) or ():
            raw_name = getattr(sibling, "rfilename", None)
            if not isinstance(raw_name, str) or not any(
                fnmatch.fnmatch(raw_name, pattern) for pattern in allow_patterns
            ):
                continue
            name = cls._validated_relative_name(raw_name)
            if name in files:
                raise RuntimeError(f"模型元数据包含重复文件：{name}")

            declared_size = getattr(sibling, "size", None)
            lfs = getattr(sibling, "lfs", None)
            lfs_size = getattr(lfs, "size", None) if lfs is not None else None
            if (
                declared_size is not None
                and lfs_size is not None
                and int(declared_size) != int(lfs_size)
            ):
                raise RuntimeError(f"模型元数据文件大小不一致：{name}")
            size_value = lfs_size if lfs_size is not None else declared_size
            try:
                size = int(size_value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"模型元数据缺少文件大小：{name}") from exc
            if size <= 0:
                raise RuntimeError(f"模型元数据文件大小无效：{name}")

            metadata: dict[str, object] = {"size": size}
            raw_sha256 = getattr(lfs, "sha256", None) if lfs is not None else None
            if raw_sha256:
                sha256 = str(raw_sha256).strip().lower()
                if len(sha256) != 64 or any(
                    character not in "0123456789abcdef" for character in sha256
                ):
                    raise RuntimeError(f"模型元数据 SHA-256 无效：{name}")
                metadata["sha256"] = sha256
            files[name] = metadata

        missing = sorted(set(required_files) - set(files))
        if missing:
            raise RuntimeError(f"模型元数据缺少必需文件：{', '.join(missing)}")
        return files

    @classmethod
    def _verify_model_files(
        cls,
        path: Path,
        spec: ModelSpec,
        manifest: dict[str, object],
        *,
        verify_hashes: bool,
    ) -> None:
        if manifest.get("manifest_version") != _MANIFEST_VERSION:
            raise RuntimeError("模型清单版本不受支持")
        if manifest.get("repo_id") != spec.repo_id or manifest.get("revision") != spec.revision:
            raise RuntimeError("模型清单与固定仓库版本不一致")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise RuntimeError("模型清单缺少文件列表")
        cls._verify_files(
            path,
            files,
            required_files=_WHISPER_REQUIRED_FILES,
            verify_hashes=verify_hashes,
        )

    @classmethod
    def _verify_bundle_model(
        cls,
        path: Path,
        spec: ModelSpec,
        manifest: dict[str, object],
        *,
        verify_hashes: bool,
    ) -> None:
        if manifest.get("manifest_version") != _BUNDLE_MANIFEST_VERSION:
            raise RuntimeError("组合模型清单版本不受支持")
        if manifest.get("model_id") != spec.id:
            raise RuntimeError("组合模型清单与所选模型不一致")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            raise RuntimeError("组合模型清单缺少组件列表")

        expected_names = {artifact.name for artifact in spec.artifacts}
        actual_names = set(artifacts)
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            details: list[str] = []
            if missing:
                details.append(f"缺少 {', '.join(missing)}")
            if extra:
                details.append(f"多出 {', '.join(extra)}")
            raise RuntimeError(f"组合模型组件不一致：{'；'.join(details)}")

        for artifact in spec.artifacts:
            raw_manifest = artifacts.get(artifact.name)
            if not isinstance(raw_manifest, dict):
                raise RuntimeError(f"模型组件清单无效：{artifact.name}")
            if (
                raw_manifest.get("repo_id") != artifact.repo_id
                or raw_manifest.get("revision") != artifact.revision
            ):
                raise RuntimeError(f"模型组件 {artifact.name} 与固定仓库版本不一致")
            files = raw_manifest.get("files")
            if not isinstance(files, dict):
                raise RuntimeError(f"模型组件 {artifact.name} 缺少文件列表")
            cls._verify_files(
                path / artifact.name,
                files,
                required_files=artifact.required_files,
                verify_hashes=verify_hashes,
            )

    @classmethod
    def _verify_files(
        cls,
        path: Path,
        files: dict[object, object],
        *,
        required_files: tuple[str, ...],
        verify_hashes: bool,
    ) -> None:
        missing = sorted(set(required_files) - set(files))
        if missing:
            raise RuntimeError(f"模型清单缺少必需文件：{', '.join(missing)}")

        root = path.resolve()
        for raw_name, raw_metadata in files.items():
            name = cls._validated_relative_name(raw_name)
            if not isinstance(raw_metadata, dict):
                raise RuntimeError(f"模型清单文件信息无效：{name}")
            expected_size = raw_metadata.get("size")
            if type(expected_size) is not int or expected_size <= 0:
                raise RuntimeError(f"模型清单文件大小无效：{name}")
            expected_sha256 = raw_metadata.get("sha256")
            if expected_sha256 is not None and (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(
                    character not in "0123456789abcdef" for character in expected_sha256.lower()
                )
            ):
                raise RuntimeError(f"模型清单 SHA-256 无效：{name}")

            candidate = path / Path(*PurePosixPath(name).parts)
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise RuntimeError(f"模型文件缺失：{name}") from exc
            if root not in resolved.parents or not resolved.is_file():
                raise RuntimeError(f"模型文件路径越界：{name}")
            actual_size = resolved.stat().st_size
            if actual_size != expected_size:
                raise RuntimeError(
                    f"模型文件大小校验失败：{name}（期望 {expected_size}，实际 {actual_size}）"
                )
            if verify_hashes and expected_sha256:
                actual_sha256 = cls._sha256(resolved)
                if actual_sha256 != expected_sha256.lower():
                    raise RuntimeError(f"模型文件 SHA-256 校验失败：{name}")

    @classmethod
    def _bundle_download_is_incomplete(
        cls,
        path: Path,
        spec: ModelSpec,
        manifest: dict[str, object],
    ) -> bool:
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            return False
        for artifact in spec.artifacts:
            artifact_manifest = artifacts.get(artifact.name)
            if not isinstance(artifact_manifest, dict):
                return False
            files = artifact_manifest.get("files")
            if not isinstance(files, dict):
                return False
            if cls._download_is_incomplete(path / artifact.name, artifact_manifest):
                return True
        return False

    @classmethod
    def _download_is_incomplete(
        cls,
        path: Path,
        manifest: dict[str, object],
    ) -> bool:
        files = manifest.get("files")
        if not isinstance(files, dict):
            return False
        root = path.resolve()
        for raw_name, raw_metadata in files.items():
            try:
                name = cls._validated_relative_name(raw_name)
            except RuntimeError:
                return False
            if not isinstance(raw_metadata, dict):
                return False
            expected_size = raw_metadata.get("size")
            if type(expected_size) is not int or expected_size <= 0:
                return False
            candidate = path / Path(*PurePosixPath(name).parts)
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                return True
            if root not in resolved.parents or not resolved.is_file():
                return False
            if resolved.stat().st_size < expected_size:
                return True
        return False

    @staticmethod
    def _validated_relative_name(value: object) -> str:
        if not isinstance(value, str) or not value or "\\" in value:
            raise RuntimeError("模型清单包含无效文件名")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise RuntimeError("模型清单包含越界文件名")
        return path.as_posix()

    @staticmethod
    def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
        (path / _MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, object]:
        manifest_path = path / _MANIFEST_FILENAME
        if not manifest_path.is_file():
            manifest_path = path / _LEGACY_MANIFEST_FILENAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("模型清单缺失或损坏") from exc
        if not isinstance(manifest, dict):
            raise RuntimeError("模型清单格式无效")
        return manifest

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _is_valid_model(cls, path: Path, spec: ModelSpec) -> bool:
        if not path.is_dir():
            return False
        try:
            manifest = cls._read_manifest(path)
            if spec.artifacts:
                cls._verify_bundle_model(path, spec, manifest, verify_hashes=False)
            else:
                cls._verify_model_files(path, spec, manifest, verify_hashes=False)
            if not spec.artifacts and not (path / _MANIFEST_FILENAME).is_file():
                with suppress(OSError):
                    cls._write_manifest(path, manifest)
            return True
        except (OSError, RuntimeError):
            return False

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, RuntimeError) and str(exc):
            return str(exc)
        return "模型下载失败，请检查网络后重试"
