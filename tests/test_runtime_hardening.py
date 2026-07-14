import sys
from pathlib import Path

import pytest

import sublingo_local.__main__ as cli
from sublingo_local import system


@pytest.mark.parametrize("host", ["127.0.0.1", "127.12.34.56", "localhost", "::1", "[::1]"])
def test_loopback_hosts_are_recognized(host: str) -> None:
    assert cli._is_loopback_host(host)  # noqa: SLF001


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "service.internal"])
def test_non_loopback_hosts_are_recognized(host: str) -> None:
    assert not cli._is_loopback_host(host)  # noqa: SLF001


def test_cli_rejects_non_loopback_host_without_session_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAPTIONNEST_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["captionnest", "--host", "0.0.0.0"])
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda *args, **kwargs: pytest.fail("拒绝不安全监听后不得启动 Uvicorn"),
    )

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2


def test_cli_allows_non_loopback_host_with_session_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setenv("CAPTIONNEST_SESSION_TOKEN", "desktop-secret")
    monkeypatch.setattr(sys, "argv", ["captionnest", "--host", "0.0.0.0", "--port", "9999"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured["app"] == "sublingo_local.app:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999


def test_windows_open_folder_uses_explorer_parameter_array(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "output"
    folder.mkdir()
    subtitle = folder / "movie.srt"
    subtitle.write_text("subtitle", encoding="utf-8")
    executable = r"C:\Windows\explorer.exe"
    captured: dict[str, object] = {}

    def fake_popen(args: list[str], **kwargs: object) -> object:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(system.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        system.shutil,
        "which",
        lambda command: executable if command == "explorer.exe" else None,
    )
    monkeypatch.setattr(system.subprocess, "Popen", fake_popen)

    result = system.open_folder(str(subtitle))

    assert captured["args"] == [executable, str(folder.resolve())]
    assert captured["kwargs"] == {
        "shell": False,
        "stdout": system.subprocess.DEVNULL,
        "stderr": system.subprocess.DEVNULL,
    }
    assert result.opened
    assert result.path == str(folder.resolve())
