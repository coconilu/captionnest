from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAURI_CLI_VERSION = "2.11.4"
TAURI_INSTALLER_BASELINE_SHA256 = (
    "20f4ecc730defb71f1342eaeaec4021df13be3d843abba0effe88ea5835fa079"
)

CAPTIONNEST_TEMPLATE_HEADER = """; Based on Tauri CLI 2.11.4's official installer template:
; https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi
; CaptionNest customization: upgrades default to an in-place install so a
; vulnerable older uninstaller cannot delete app-managed recognition models.

"""

CAPTIONNEST_UPGRADE_DEFAULT = (
    "    ; An in-place install is the safe default for upgrades. In particular, "
    "the\n"
    "    ; first fixed installer must not execute an older uninstaller whose "
    "custom\n"
    "    ; hook deleted recognition models unconditionally.\n"
    "    !insertmacro CAPTIONNEST_SET_REINSTALL_DEFAULT $R0 "
    "$ReinstallPageCheck\n"
    "    ${If} $ReinstallPageCheck = 2\n"
    "      SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0\n"
    "      ${NSD_SetFocus} $R3\n"
    "    ${Else}\n"
    "      SendMessage $R2 ${BM_SETCHECK} ${BST_CHECKED} 0\n"
    "      ${NSD_SetFocus} $R2\n"
    "    ${EndIf}\n\n"
)

TAURI_UPGRADE_DEFAULT = """    ; Check the first radio button if this the first time
    ; we enter this page or if the second button wasn't
    ; selected the last time we were on this page
    ${If} $ReinstallPageCheck <> 2
      SendMessage $R2 ${BM_SETCHECK} ${BST_CHECKED} 0
    ${Else}
      SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0
    ${EndIf}

    ${NSD_SetFocus} $R2
"""

CAPTIONNEST_UNATTENDED_UPGRADE = (
    "  ; Silent and passive installs cannot collect an explicit data-deletion\n"
    "  ; decision, so they always install in place and preserve existing models.\n"
    "  !insertmacro CAPTIONNEST_SKIP_UNINSTALL_FOR_UNATTENDED $PassiveMode "
    "reinst_done\n\n"
)

CAPTIONNEST_REINSTALL_STATE_ORDER = """Function PageLeaveReinstall
  ; If migrating from Wix, always uninstall
  ${If} $WixMode = 1
    Goto reinst_uninstall
  ${EndIf}

  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_done
  ${EndIf}

  ${NSD_GetState} $R2 $R1
"""

TAURI_REINSTALL_STATE_ORDER = """Function PageLeaveReinstall
  ${NSD_GetState} $R2 $R1

  ; If migrating from Wix, always uninstall
  ${If} $WixMode = 1
    Goto reinst_uninstall
  ${EndIf}

  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_done
  ${EndIf}
"""


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
        if os.environ.get("CAPTIONNEST_REQUIRE_NSIS_TESTS") == "1":
            pytest.fail("required Windows NSIS lifecycle harness is unavailable")
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
        if os.environ.get("CAPTIONNEST_REQUIRE_NSIS_TESTS") == "1":
            pytest.fail("required Windows NSIS lifecycle harness is unavailable")
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


def test_custom_nsis_template_matches_offline_tauri_baseline() -> None:
    template = _installer_template()
    assert template.count(CAPTIONNEST_TEMPLATE_HEADER) == 1
    assert template.count(CAPTIONNEST_UPGRADE_DEFAULT) == 1
    assert template.count(CAPTIONNEST_UNATTENDED_UPGRADE) == 1

    normalized = template.replace(CAPTIONNEST_TEMPLATE_HEADER, "", 1)
    normalized = normalized.replace(
        CAPTIONNEST_UPGRADE_DEFAULT,
        TAURI_UPGRADE_DEFAULT,
        1,
    )
    normalized = normalized.replace(CAPTIONNEST_UNATTENDED_UPGRADE, "", 1)
    assert normalized.count(CAPTIONNEST_REINSTALL_STATE_ORDER) == 1
    normalized = normalized.replace(
        CAPTIONNEST_REINSTALL_STATE_ORDER,
        TAURI_REINSTALL_STATE_ORDER,
        1,
    )
    assert (
        hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        == TAURI_INSTALLER_BASELINE_SHA256
    )

    lock = json.loads(
        (PROJECT_ROOT / "apps" / "web" / "package-lock.json").read_text(
            encoding="utf-8"
        )
    )
    packages = lock["packages"]
    assert packages["node_modules/@tauri-apps/cli"]["version"] == TAURI_CLI_VERSION
    assert (
        packages["node_modules/@tauri-apps/cli-win32-x64-msvc"]["version"]
        == TAURI_CLI_VERSION
    )


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
