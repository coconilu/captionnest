from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _installer_hooks() -> tuple[str, str]:
    config = json.loads(
        (PROJECT_ROOT / "apps" / "desktop" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    hooks_path = (
        PROJECT_ROOT
        / "apps"
        / "desktop"
        / config["bundle"]["windows"]["nsis"]["installerHooks"]
    )
    hooks = hooks_path.read_text(encoding="utf-8")
    return config["identifier"], hooks


def _installer_template() -> str:
    config = json.loads(
        (PROJECT_ROOT / "apps" / "desktop" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    template_path = (
        PROJECT_ROOT
        / "apps"
        / "desktop"
        / config["bundle"]["windows"]["nsis"]["template"]
    )
    return template_path.read_text(encoding="utf-8")


def _makensis_path() -> Path | None:
    executable = shutil.which("makensis") or shutil.which("makensis.exe")
    if executable:
        return Path(executable)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "tauri" / "NSIS" / "makensis.exe"
        if candidate.is_file():
            return candidate
    return None


def _nsis_path(path: Path) -> str:
    resolved = str(path.resolve())
    assert '"' not in resolved
    assert "$" not in resolved
    return resolved


def _run_uninstall_policy_harness(
    tmp_path: Path,
    *,
    delete_app_data: bool,
    update_mode: bool,
) -> bool:
    makensis = _makensis_path()
    if os.name != "nt" or makensis is None:
        pytest.skip("NSIS lifecycle harness requires Windows and makensis")

    models_dir = tmp_path / "models"
    marker = models_dir / "model.marker"
    models_dir.mkdir()
    marker.write_text("existing-model", encoding="utf-8")

    hooks_path = PROJECT_ROOT / "apps" / "desktop" / "windows" / "installer-hooks.nsh"
    harness = tmp_path / "model-retention-harness.nsi"
    harness.write_text(
        "\n".join(
            [
                "Unicode true",
                "!include LogicLib.nsh",
                "Var DeleteAppDataCheckboxState",
                "Var UpdateMode",
                f'!define CAPTIONNEST_MODELS_DIR "{_nsis_path(models_dir)}"',
                f'!include "{_nsis_path(hooks_path)}"',
                'Name "CaptionNest model retention harness"',
                f'OutFile "{_nsis_path(tmp_path / "harness.exe")}"',
                "RequestExecutionLevel user",
                "SilentInstall silent",
                "Section",
                f"  StrCpy $DeleteAppDataCheckboxState {int(delete_app_data)}",
                f"  StrCpy $UpdateMode {int(update_mode)}",
                "  !insertmacro NSIS_HOOK_POSTUNINSTALL",
                "SectionEnd",
                "",
            ]
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [str(makensis), "/V2", str(harness)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executed = subprocess.run(
        [str(tmp_path / "harness.exe"), "/S"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    return marker.exists()


def _run_upgrade_policy_harness(
    tmp_path: Path,
    *,
    silent: bool,
    passive: bool,
    explicit_uninstall: bool,
) -> bool:
    makensis = _makensis_path()
    if os.name != "nt" or makensis is None:
        pytest.skip("NSIS lifecycle harness requires Windows and makensis")

    models_dir = tmp_path / "models"
    marker = models_dir / "model.marker"
    models_dir.mkdir()
    marker.write_text("existing-model", encoding="utf-8")

    policy_path = (
        PROJECT_ROOT
        / "apps"
        / "desktop"
        / "windows"
        / "model-retention-policy.nsh"
    )
    harness = tmp_path / "upgrade-policy-harness.nsi"
    harness.write_text(
        "\n".join(
            [
                "Unicode true",
                "!include LogicLib.nsh",
                f'!include "{_nsis_path(policy_path)}"',
                "Var PassiveMode",
                "Var SimulatedSilentMode",
                "Var ReinstallPageCheck",
                'Name "CaptionNest upgrade policy harness"',
                f'OutFile "{_nsis_path(tmp_path / "upgrade-harness.exe")}"',
                "RequestExecutionLevel user",
                "SilentInstall silent",
                "Section",
                f"  StrCpy $PassiveMode {int(passive)}",
                f"  StrCpy $SimulatedSilentMode {int(silent)}",
                "  StrCpy $R0 1",
                '  StrCpy $ReinstallPageCheck ""',
                "  !insertmacro CAPTIONNEST_SET_REINSTALL_DEFAULT $R0 $ReinstallPageCheck",
                *(
                    ["  StrCpy $ReinstallPageCheck 1"]
                    if explicit_uninstall
                    else []
                ),
                (
                    "  !insertmacro CAPTIONNEST_SKIP_UNINSTALL_FOR_MODE "
                    "$SimulatedSilentMode $PassiveMode upgrade_done"
                ),
                "  ${If} $ReinstallPageCheck = 1",
                f'    RMDir /r "{_nsis_path(models_dir)}"',
                "  ${EndIf}",
                "  upgrade_done:",
                "SectionEnd",
                "",
            ]
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [str(makensis), "/V2", str(harness)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executed = subprocess.run(
        [str(tmp_path / "upgrade-harness.exe"), "/S"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    return marker.exists()


def test_nsis_uninstall_reuses_tauri_delete_app_data_decision() -> None:
    identifier, hooks = _installer_hooks()

    assert "NSIS_HOOK_PREUNINSTALL" not in hooks
    assert "MessageBox" not in hooks
    assert "NSIS_HOOK_POSTUNINSTALL" in hooks
    assert "$DeleteAppDataCheckboxState = 1" in hooks
    assert "$UpdateMode <> 1" in hooks
    assert f"$LOCALAPPDATA\\{identifier}\\models" in hooks
    assert f"$LOCALAPPDATA\\{identifier}\"" not in hooks


def test_nsis_upgrade_defaults_preserve_models_before_old_uninstaller_runs() -> None:
    template = _installer_template()

    upgrade_choice = template.split(
        "; An in-place install is the safe default for upgrades.", 1
    )[1].split("nsDialogs::Show", 1)[0]
    assert (
        "!insertmacro CAPTIONNEST_SET_REINSTALL_DEFAULT $R0 $ReinstallPageCheck"
        in upgrade_choice
    )
    assert "${NSD_SetFocus} $R3" in upgrade_choice

    leave_reinstall = template.split("Function PageLeaveReinstall", 1)[1].split(
        "FunctionEnd", 1
    )[0]
    assert leave_reinstall.index("$UpdateMode = 1") < leave_reinstall.index(
        "CAPTIONNEST_SKIP_UNINSTALL_FOR_UNATTENDED"
    ) < leave_reinstall.index("${NSD_GetState}")
    assert "StrCpy $R1 \"$R1 /UPDATE\"" in template

    policy = (
        PROJECT_ROOT
        / "apps"
        / "desktop"
        / "windows"
        / "model-retention-policy.nsh"
    ).read_text(encoding="utf-8")
    assert "${If} ${VersionComparison} = 1" in policy
    assert "StrCpy ${Selection} 2" in policy
    assert "IfSilent 0 +2" in policy
    assert "CAPTIONNEST_SKIP_UNINSTALL_FOR_MODE" in policy
    assert "${If} ${PassiveMode} = 1" in policy


@pytest.mark.parametrize(
    ("silent", "passive", "explicit_uninstall", "marker_survives"),
    [
        pytest.param(False, False, False, True, id="normal-upgrade-default"),
        pytest.param(True, False, False, True, id="silent-upgrade"),
        pytest.param(False, True, False, True, id="passive-upgrade"),
        pytest.param(False, False, True, False, id="explicit-old-uninstall"),
    ],
)
def test_nsis_upgrade_control_flow_protects_marker_from_old_uninstaller(
    tmp_path: Path,
    silent: bool,
    passive: bool,
    explicit_uninstall: bool,
    marker_survives: bool,
) -> None:
    assert (
        _run_upgrade_policy_harness(
            tmp_path,
            silent=silent,
            passive=passive,
            explicit_uninstall=explicit_uninstall,
        )
        is marker_survives
    )


@pytest.mark.parametrize(
    ("delete_app_data", "update_mode", "marker_survives"),
    [
        pytest.param(True, True, True, id="update-mode-forces-keep"),
        pytest.param(False, False, True, id="explicit-keep"),
        pytest.param(True, False, False, id="explicit-delete"),
    ],
)
def test_nsis_model_marker_follows_uninstall_decision(
    tmp_path: Path,
    delete_app_data: bool,
    update_mode: bool,
    marker_survives: bool,
) -> None:
    assert (
        _run_uninstall_policy_harness(
            tmp_path,
            delete_app_data=delete_app_data,
            update_mode=update_mode,
        )
        is marker_survives
    )
