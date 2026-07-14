from pathlib import Path
from types import SimpleNamespace

import pytest

from sublingo_local import environment
from sublingo_local.environment import EnvironmentService
from sublingo_local.model_manager import ModelManager


def _service(tmp_path: Path) -> EnvironmentService:
    return EnvironmentService(ModelManager(tmp_path / "models"))


def test_default_environment_model_is_cpu_friendly(tmp_path: Path) -> None:
    result = _service(tmp_path)._check_model()  # noqa: SLF001

    assert result.name == "small"
    assert result.status == "missing"


def test_codex_status_is_not_installed_without_executing_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("Codex is missing, so no subprocess should be started")

    monkeypatch.setattr(environment.shutil, "which", lambda command: None)
    monkeypatch.setattr(environment.subprocess, "run", unexpected_run)

    result = _service(tmp_path)._check_codex()  # noqa: SLF001

    assert result.status == "not_installed"
    assert result.version is None
    assert result.install_url == EnvironmentService.CODEX_INSTALL_URL


@pytest.mark.parametrize(
    ("login_returncode", "expected_status"),
    [(1, "not_logged_in"), (0, "ready")],
)
def test_codex_status_uses_version_and_login_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    login_returncode: int,
    expected_status: str,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        if args[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.144.1\n", stderr="")
        return SimpleNamespace(returncode=login_returncode, stdout="", stderr="")

    executable = r"C:\Program Files\Codex\codex.exe"
    monkeypatch.setattr(
        environment.shutil,
        "which",
        lambda command: executable if command == "codex" else None,
    )
    monkeypatch.setattr(environment.subprocess, "run", fake_run)

    result = _service(tmp_path)._check_codex()  # noqa: SLF001

    assert result.status == expected_status
    assert result.version == "0.144.1"
    assert [args for args, _ in calls] == [
        [executable, "--version"],
        [executable, "login", "status"],
    ]
    for _, options in calls:
        assert options["shell"] is False
        assert options["timeout"] == 10
        assert options["check"] is False


def test_codex_status_is_check_failed_when_process_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(environment.shutil, "which", lambda command: "codex.exe")

    def fail_run(args: list[str], **kwargs: object) -> None:
        raise OSError("process unavailable")

    monkeypatch.setattr(environment.subprocess, "run", fail_run)

    result = _service(tmp_path)._check_codex()  # noqa: SLF001

    assert result.status == "check_failed"
    assert result.version is None
    assert result.install_url == EnvironmentService.CODEX_INSTALL_URL


@pytest.mark.parametrize(
    ("missing_libraries", "expected_status", "expected_available"),
    [([], "cuda_ready", True), (["cudnn64_9.dll"], "cuda_unavailable", False)],
)
def test_cuda_requires_both_device_and_runtime_libraries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_libraries: list[str],
    expected_status: str,
    expected_available: bool,
) -> None:
    fake_ctranslate2 = SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setattr(
        environment.importlib,
        "import_module",
        lambda name: fake_ctranslate2 if name == "ctranslate2" else None,
    )
    monkeypatch.setattr(
        EnvironmentService,
        "_missing_cuda_libraries",
        staticmethod(lambda: missing_libraries),
    )

    result = _service(tmp_path)._check_acceleration()  # noqa: SLF001

    assert result.status == expected_status
    assert result.cuda_available is expected_available
