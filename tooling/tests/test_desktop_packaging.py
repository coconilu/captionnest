from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_nsis_uninstall_warns_before_removing_app_managed_models() -> None:
    config = json.loads(
        (PROJECT_ROOT / "apps" / "desktop" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    identifier = config["identifier"]
    hooks_path = (
        PROJECT_ROOT
        / "apps"
        / "desktop"
        / config["bundle"]["windows"]["nsis"]["installerHooks"]
    )
    hooks = hooks_path.read_text(encoding="utf-8")

    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert "MB_OKCANCEL" in hooks
    assert "/SD IDOK IDOK continue_uninstall" in hooks
    assert "Abort" in hooks
    assert "NSIS_HOOK_POSTUNINSTALL" in hooks
    assert f"$LOCALAPPDATA\\{identifier}\\models" in hooks
    assert f"$LOCALAPPDATA\\{identifier}\"" not in hooks
