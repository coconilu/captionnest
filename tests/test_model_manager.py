import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sublingo_local.model_manager import MODEL_SPECS, ModelManager

_SMALL_REPO_ID = "Systran/faster-whisper-small"
_SMALL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
_QWEN_ASR_REPO_ID = "Qwen/Qwen3-ASR-1.7B"
_QWEN_ASR_REVISION = "7278e1e70fe206f11671096ffdd38061171dd6e5"
_QWEN_ALIGNER_REPO_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
_QWEN_ALIGNER_REVISION = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
_MANIFEST_FILENAME = ".captionnest-model-manifest.json"
_LEGACY_MANIFEST_FILENAME = ".sublingo-model-manifest.json"
_MODEL_FILES = {
    "config.json": b"test",
    "model.bin": b"test",
    "tokenizer.json": b"test",
}
_QWEN_FILES = {
    _QWEN_ASR_REPO_ID: {
        "config.json": b"{}",
        "model-00001-of-00002.safetensors": b"asr-1",
        "model-00002-of-00002.safetensors": b"asr-2",
        "model.safetensors.index.json": b"{}",
        "preprocessor_config.json": b"{}",
        "tokenizer_config.json": b"{}",
        "merges.txt": b"merge",
        "vocab.json": b"{}",
    },
    _QWEN_ALIGNER_REPO_ID: {
        "config.json": b"{}",
        "model.safetensors": b"aligner",
        "preprocessor_config.json": b"{}",
        "tokenizer_config.json": b"{}",
        "merges.txt": b"merge",
        "vocab.json": b"{}",
    },
}


def _write_valid_model(path: Path, files: dict[str, bytes] | None = None) -> None:
    payloads = files or _MODEL_FILES
    path.mkdir(parents=True, exist_ok=True)
    for name, content in payloads.items():
        (path / name).write_bytes(content)
    manifest = {
        "manifest_version": 1,
        "repo_id": _SMALL_REPO_ID,
        "revision": _SMALL_REVISION,
        "files": {
            name: {
                "size": len(content),
                **(
                    {"sha256": hashlib.sha256(content).hexdigest()}
                    if name == "model.bin"
                    else {}
                ),
            }
            for name, content in payloads.items()
        },
    }
    (path / _MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _model_info(*, model_sha256: str | None = None) -> SimpleNamespace:
    siblings = []
    for name, content in _MODEL_FILES.items():
        lfs = (
            SimpleNamespace(size=len(content), sha256=model_sha256)
            if name == "model.bin" and model_sha256
            else None
        )
        siblings.append(SimpleNamespace(rfilename=name, size=len(content), lfs=lfs))
    return SimpleNamespace(sha=_SMALL_REVISION, siblings=siblings)


def test_model_specs_pin_expected_revisions() -> None:
    assert {spec.id: spec.revision for spec in MODEL_SPECS} == {
        "small": "536b0662742c02347bc0e980a01041f333bce120",
        "medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
        "large-v3-turbo": "0c94664816ec82be77b20e824c8e8675995b0029",
        "large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "qwen3-asr-1.7b": _QWEN_ASR_REVISION,
    }


def test_model_manager_reports_missing_damaged_and_ready(tmp_path: Path) -> None:
    manager = ModelManager(tmp_path / "models")

    missing = manager.get("small")
    assert missing.status == "missing"
    assert missing.path is None

    model_path = manager.model_path("small")
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    damaged = manager.get("small")
    assert damaged.status == "damaged"
    assert damaged.path == str(model_path)
    with pytest.raises(RuntimeError, match="尚未下载"):
        manager.resolve_installed_path("small")

    _write_valid_model(model_path)
    ready = manager.get("small")
    assert ready.status == "ready"
    assert ready.progress == 100
    assert manager.resolve_installed_path("small") == model_path

    (model_path / "model.bin").write_bytes(b"")
    assert manager.get("small").status == "damaged"
    (model_path / "model.bin").write_bytes(b"test")

    manifest_path = model_path / _MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["revision"] = "moving-main"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert manager.get("small").status == "damaged"
    manifest["revision"] = _SMALL_REVISION
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert manager.get("small").status == "ready"

    listing = manager.list()
    assert listing.model_root == str(manager.root)
    assert {item.id for item in listing.items} == {
        "small",
        "medium",
        "large-v3-turbo",
        "large-v3",
        "qwen3-asr-1.7b",
    }


def test_model_manager_reads_and_migrates_legacy_manifest(tmp_path: Path) -> None:
    manager = ModelManager(tmp_path / "models")
    model_path = manager.model_path("small")
    _write_valid_model(model_path)
    current_manifest = model_path / _MANIFEST_FILENAME
    current_manifest.replace(model_path / _LEGACY_MANIFEST_FILENAME)

    assert manager.get("small").status == "ready"
    assert current_manifest.is_file()


def test_model_manager_does_not_migrate_invalid_legacy_manifest(tmp_path: Path) -> None:
    manager = ModelManager(tmp_path / "models")
    model_path = manager.model_path("small")
    _write_valid_model(model_path)
    current_manifest = model_path / _MANIFEST_FILENAME
    legacy_manifest = model_path / _LEGACY_MANIFEST_FILENAME
    current_manifest.replace(legacy_manifest)
    manifest = json.loads(legacy_manifest.read_text(encoding="utf-8"))
    manifest["revision"] = "moving-main"
    legacy_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    assert manager.get("small").status == "damaged"
    assert not current_manifest.exists()


def test_model_manager_downloads_into_app_owned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeApi:
        def model_info(self, repo_id, *, revision, files_metadata):  # type: ignore[no-untyped-def]
            captured["repo_id"] = repo_id
            captured["revision"] = revision
            captured["files_metadata"] = files_metadata
            return _model_info(
                model_sha256=hashlib.sha256(_MODEL_FILES["model.bin"]).hexdigest()
            )

    def fake_snapshot_download(  # type: ignore[no-untyped-def]
        *, repo_id, revision, local_dir, allow_patterns
    ):
        captured["local_dir"] = local_dir
        captured["allow_patterns"] = allow_patterns
        for name, content in _MODEL_FILES.items():
            (Path(local_dir) / name).write_bytes(content)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=FakeApi, snapshot_download=fake_snapshot_download),
    )
    manager = ModelManager(tmp_path / "models")

    started = manager.start_download("small")
    assert started.status in {"downloading", "ready"}
    manager._threads["small"].join(timeout=2)  # noqa: SLF001

    result = manager.get("small")
    assert result.status == "ready"
    assert result.path == str(manager.root / "small")
    assert captured["repo_id"] == _SMALL_REPO_ID
    assert captured["revision"] == _SMALL_REVISION
    assert Path(captured["local_dir"]).parent == manager.root / ".downloads"
    assert "model.bin" in captured["allow_patterns"]
    manifest = json.loads(
        (manager.root / "small" / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["repo_id"] == _SMALL_REPO_ID
    assert manifest["revision"] == _SMALL_REVISION
    assert manifest["files"]["model.bin"]["sha256"] == hashlib.sha256(b"test").hexdigest()

    monkeypatch.setattr(
        ModelManager,
        "_sha256",
        staticmethod(lambda path: pytest.fail(f"日常状态检查不应重新哈希 {path}")),
    )
    assert manager.get("small").status == "ready"


@pytest.mark.parametrize(
    ("corruption", "expected_message"),
    [("size", "大小校验失败"), ("hash", "SHA-256 校验失败")],
)
def test_model_manager_rejects_download_with_size_or_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
    expected_message: str,
) -> None:
    expected_hash = hashlib.sha256(_MODEL_FILES["model.bin"]).hexdigest()

    class FakeApi:
        def model_info(self, repo_id, *, revision, files_metadata):  # type: ignore[no-untyped-def]
            return _model_info(model_sha256=expected_hash)

    def fake_snapshot_download(  # type: ignore[no-untyped-def]
        *, repo_id, revision, local_dir, allow_patterns
    ) -> None:
        payloads = dict(_MODEL_FILES)
        payloads["model.bin"] = b"wrong" if corruption == "size" else b"fail"
        for name, content in payloads.items():
            (Path(local_dir) / name).write_bytes(content)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=FakeApi, snapshot_download=fake_snapshot_download),
    )
    manager = ModelManager(tmp_path / "models")

    manager.start_download("small")
    manager._threads["small"].join(timeout=2)  # noqa: SLF001

    result = manager.get("small")
    assert result.status == "missing"
    assert expected_message in (result.message or "")
    assert not manager.model_path("small").exists()
    assert not list((manager.root / ".downloads").iterdir())


def test_model_manager_restores_previous_model_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_hash = hashlib.sha256(_MODEL_FILES["model.bin"]).hexdigest()

    class FakeApi:
        def model_info(self, repo_id, *, revision, files_metadata):  # type: ignore[no-untyped-def]
            return _model_info(model_sha256=expected_hash)

    def fake_snapshot_download(  # type: ignore[no-untyped-def]
        *, repo_id, revision, local_dir, allow_patterns
    ) -> None:
        for name, content in _MODEL_FILES.items():
            (Path(local_dir) / name).write_bytes(content)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=FakeApi, snapshot_download=fake_snapshot_download),
    )
    manager = ModelManager(tmp_path / "models")
    previous_files = {
        "config.json": b"old!",
        "model.bin": b"old!",
        "tokenizer.json": b"old!",
    }
    destination = manager.model_path("small")
    _write_valid_model(destination, previous_files)

    original_replace = Path.replace

    def fail_install_once(self: Path, target: Path) -> Path:
        if (
            self.parent == manager.root / ".downloads"
            and self.name.startswith("small-")
            and "-backup-" not in self.name
            and Path(target) == destination
        ):
            raise OSError("simulated atomic replacement failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_install_once)
    manager.start_download("small")
    manager._threads["small"].join(timeout=2)  # noqa: SLF001

    result = manager.get("small")
    assert result.status == "ready"
    assert "继续使用已有版本" in (result.message or "")
    assert (destination / "model.bin").read_bytes() == b"old!"
    assert not list((manager.root / ".downloads").iterdir())


def test_model_manager_resumes_transport_failure_from_revision_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    resumed = False

    class FakeApi:
        def model_info(self, repo_id, *, revision, files_metadata):  # type: ignore[no-untyped-def]
            return _model_info()

    def fake_snapshot_download(  # type: ignore[no-untyped-def]
        *, repo_id, revision, local_dir, allow_patterns
    ) -> None:
        nonlocal attempts, resumed
        attempts += 1
        destination = Path(local_dir)
        destination.mkdir(parents=True, exist_ok=True)
        partial = destination / ".cache" / "huggingface" / "download" / "model.incomplete"
        if attempts == 1:
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"partial bytes")
            raise OSError("simulated network disconnect")
        resumed = partial.read_bytes() == b"partial bytes"
        for name, content in _MODEL_FILES.items():
            (destination / name).write_bytes(content)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=FakeApi, snapshot_download=fake_snapshot_download),
    )
    manager = ModelManager(tmp_path / "models")

    manager.start_download("small")
    manager._threads["small"].join(timeout=2)  # noqa: SLF001
    assert manager.get("small").status == "missing"
    assert any(manager._downloads_root.iterdir())  # noqa: SLF001

    manager.start_download("small")
    manager._threads["small"].join(timeout=2)  # noqa: SLF001

    assert resumed is True
    assert manager.get("small").status == "ready"
    assert not list(manager._downloads_root.iterdir())  # noqa: SLF001


def test_model_manager_keeps_partial_when_hub_silently_falls_back_to_local_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    resumed = False

    class FakeApi:
        def model_info(self, repo_id, *, revision, files_metadata):  # type: ignore[no-untyped-def]
            return _model_info()

    def fake_snapshot_download(  # type: ignore[no-untyped-def]
        *, repo_id, revision, local_dir, allow_patterns
    ) -> None:
        nonlocal attempts, resumed
        attempts += 1
        destination = Path(local_dir)
        destination.mkdir(parents=True, exist_ok=True)
        partial = destination / ".cache" / "huggingface" / "download" / "model.incomplete"
        if attempts == 1:
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"partial bytes")
            return
        resumed = partial.read_bytes() == b"partial bytes"
        for name, content in _MODEL_FILES.items():
            (destination / name).write_bytes(content)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=FakeApi, snapshot_download=fake_snapshot_download),
    )
    manager = ModelManager(tmp_path / "models")

    manager.start_download("small")
    manager._threads["small"].join(timeout=2)  # noqa: SLF001
    assert manager.get("small").status == "missing"
    assert any(manager._downloads_root.iterdir())  # noqa: SLF001

    manager.start_download("small")
    manager._threads["small"].join(timeout=2)  # noqa: SLF001

    assert resumed is True
    assert manager.get("small").status == "ready"
    assert not list(manager._downloads_root.iterdir())  # noqa: SLF001


def test_model_manager_rejects_unknown_model_without_starting_download(tmp_path: Path) -> None:
    manager = ModelManager(tmp_path / "models")

    with pytest.raises(ValueError, match="不支持的识别模型"):
        manager.start_download("../outside")

    assert not list((manager.root / ".downloads").iterdir())


def test_model_manager_downloads_and_validates_qwen_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[tuple[str, str]] = []
    destinations: dict[str, Path] = {}

    class FakeApi:
        def model_info(self, repo_id, *, revision, files_metadata):  # type: ignore[no-untyped-def]
            assert files_metadata is True
            requests.append((repo_id, revision))
            expected_revision = (
                _QWEN_ASR_REVISION
                if repo_id == _QWEN_ASR_REPO_ID
                else _QWEN_ALIGNER_REVISION
            )
            assert revision == expected_revision
            siblings = [
                SimpleNamespace(rfilename=name, size=len(content), lfs=None)
                for name, content in _QWEN_FILES[repo_id].items()
            ]
            return SimpleNamespace(sha=revision, siblings=siblings)

    def fake_snapshot_download(  # type: ignore[no-untyped-def]
        *, repo_id, revision, local_dir, allow_patterns
    ) -> None:
        assert revision in {_QWEN_ASR_REVISION, _QWEN_ALIGNER_REVISION}
        assert "*.safetensors" in allow_patterns
        destination = Path(local_dir)
        destination.mkdir(parents=True)
        destinations[repo_id] = destination
        for name, content in _QWEN_FILES[repo_id].items():
            (destination / name).write_bytes(content)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=FakeApi, snapshot_download=fake_snapshot_download),
    )
    manager = ModelManager(tmp_path / "models")

    manager.start_download("qwen3-asr-1.7b")
    manager._threads["qwen3-asr-1.7b"].join(timeout=2)  # noqa: SLF001

    result = manager.get("qwen3-asr-1.7b")
    assert result.status == "ready"
    assert result.provider == "qwen3_asr"
    assert set(requests) == {
        (_QWEN_ASR_REPO_ID, _QWEN_ASR_REVISION),
        (_QWEN_ALIGNER_REPO_ID, _QWEN_ALIGNER_REVISION),
    }
    assert destinations[_QWEN_ASR_REPO_ID].name == "asr"
    assert destinations[_QWEN_ALIGNER_REPO_ID].name == "aligner"
    components = manager.resolve_installed_components("qwen3-asr-1.7b")
    assert components == {
        "asr": manager.root / "qwen3-asr-1.7b" / "asr",
        "aligner": manager.root / "qwen3-asr-1.7b" / "aligner",
    }
    manifest = json.loads(
        (manager.root / "qwen3-asr-1.7b" / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["manifest_version"] == 2
    assert set(manifest["artifacts"]) == {"asr", "aligner"}

    (components["aligner"] / "model.safetensors").write_bytes(b"broken")
    assert manager.get("qwen3-asr-1.7b").status == "damaged"
