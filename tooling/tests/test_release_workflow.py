import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _native_repository_paths(script: str) -> list[tuple[str, str]]:
    """Collect literal repository paths passed to native tools in a pwsh block."""
    commands: list[str] = []
    current = ""
    for line in script.splitlines():
        stripped = line.strip()
        if current:
            current = f"{current} {stripped}"
            if not stripped.endswith("`"):
                commands.append(current)
                current = ""
            continue
        if re.match(r"^&\s+(?:git|gh|python|pwsh)\b", stripped):
            current = stripped
            if not stripped.endswith("`"):
                commands.append(current)
                current = ""
    if current:
        commands.append(current)

    paths: list[tuple[str, str]] = []
    for command in commands:
        executable = re.match(r"^&\s+(\w+)", command)
        assert executable is not None
        for path in re.findall(r"'(\.[\\/][^']+)'", command):
            paths.append((executable.group(1), path))
    return paths


def test_prepare_release_owns_version_commit_tag_and_tag_ref_dispatch() -> None:
    prepare = (ROOT / ".github" / "workflows" / "prepare-release.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in prepare
    assert "ref: main" in prepare
    assert "actions: write" in prepare
    assert "contents: write" in prepare
    assert "git @('tag', '-a'" in prepare
    assert "prerelease=$Prerelease" in prepare
    assert "'--ref', $env:RELEASE_TAG" in prepare
    assert "'-f', \"version=$env:RELEASE_VERSION\"" in prepare
    assert "'-f', \"prerelease=$env:RELEASE_PRERELEASE\"" in prepare
    assert prepare.count("./scripts/check-release-workflow-contract.ps1") == 2
    assert prepare.count("'-ContractRef', 'origin/main'") == 2
    assert "legacy or unknown immutable release workflow contract" in prepare
    assert prepare.rindex("./scripts/check-release-workflow-contract.ps1") < prepare.index(
        "Dispatch tag-anchored"
    )
    existing_tag_path = prepare.split("if ($TagExists) {", 1)[1].split("} else {", 1)[0]
    assert existing_tag_path.index(
        "./scripts/check-release-workflow-contract.ps1"
    ) < existing_tag_path.index("$TagMessage = @(") < existing_tag_path.index(
        "git @('checkout', '--detach', $Tag)"
    )
    assert prepare.index("tooling/release/version.py") < prepare.index(
        "git @('commit'"
    ) < prepare.index("git @('tag', '-a'") < prepare.index("Dispatch tag-anchored")


def test_prepare_release_native_repository_paths_are_cross_platform() -> None:
    prepare = (ROOT / ".github" / "workflows" / "prepare-release.yml").read_text(
        encoding="utf-8"
    )
    prepare_step = prepare.split("- name: Prepare immutable release tag", 1)[1].split(
        "- name: Dispatch tag-anchored Windows Release", 1
    )[0]

    native_paths = _native_repository_paths(prepare_step)

    assert native_paths == [
        ("pwsh", "./scripts/check-version-consistency.ps1"),
        ("python", "./tooling/release/version.py"),
        ("pwsh", "./scripts/check-version-consistency.ps1"),
    ]
    assert all("\\" not in path for _, path in native_paths)
    version_files = prepare_step.split("$VersionFiles = @(", 1)[1].split(")", 1)[0]
    assert "\\" not in version_files


def test_release_is_anchored_to_exact_annotated_tag_and_source_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "ref: v${{ inputs.version }}" in workflow
    assert "inputs.tag" not in workflow
    assert 'if ($env:GITHUB_REF -ne "refs/tags/$Tag")' in workflow
    assert "$TagType -ne 'tag'" in workflow
    assert 'if ($env:GITHUB_SHA -ne $TagCommit)' in workflow
    assert "check-version-consistency.ps1' '-ExpectedVersion' $Version" in workflow
    assert "Update every project version" not in workflow
    assert "git @('tag'" not in workflow
    assert "git @('push'" not in workflow
    assert workflow.count("./scripts/check-release-tag-ruleset.ps1") == 2
    assert "'-Phase', 'Build start'" in workflow
    assert "'-Phase', 'Immediately before publish'" in workflow


def test_windows_release_routes_main_to_prepare_and_builds_only_exact_tag() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    route = workflow.split("- name: Route release request", 1)[1].split(
        "windows-x64:", 1
    )[0]

    assert "version:" in workflow
    assert "prerelease:" in workflow
    assert "without a v prefix" in route
    assert "$env:GITHUB_REF -eq 'refs/heads/main'" in route
    assert "'workflow', 'run', 'prepare-release.yml'" in route
    assert "'--ref', 'main'" in route
    assert "'-f', \"version=$Version\"" in route
    assert "'-f', \"prerelease=$Prerelease\"" in route
    assert '$ExpectedRef = "refs/tags/v$Version"' in route
    assert "if ($env:GITHUB_REF -ne $ExpectedRef)" in route
    assert "Run Windows Release from main or dispatch it with --ref" in route
    assert "'mode=prepare'" in route
    assert "'mode=build'" in route
    assert "needs: route" in workflow
    assert "if: needs.route.outputs.mode == 'build'" in workflow
    assert "group: windows-release-${{ github.ref }}-${{ inputs.version }}" in workflow
    assert "The tag-anchored Windows build and publish will appear as a separate" in route


def test_prepare_dispatches_tag_anchored_release_with_unprefixed_version() -> None:
    prepare = (ROOT / ".github" / "workflows" / "prepare-release.yml").read_text(
        encoding="utf-8"
    )
    dispatch = prepare.split("- name: Dispatch tag-anchored Windows Release", 1)[1]

    assert "'--ref', $env:RELEASE_TAG" in dispatch
    assert "'-f', \"version=$env:RELEASE_VERSION\"" in dispatch
    assert "'-f', \"prerelease=$env:RELEASE_PRERELEASE\"" in dispatch
    assert "tag=$env:RELEASE_TAG" not in dispatch


def test_javascript_actions_and_project_runtime_use_node_24() -> None:
    workflows = {
        path.name: path.read_text(encoding="utf-8")
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
        if path.name in {"ci.yml", "prepare-release.yml", "release.yml"}
    }
    combined = "\n".join(workflows.values())
    action_refs = re.findall(r"uses:\s+([^\s#]+)", combined)

    expected_refs = {
        "actions/checkout@v7",
        "actions/setup-node@v7",
        "actions/cache/restore@v6",
        "actions/cache/save@v6",
        "actions/upload-artifact@v7",
        "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990",
    }
    targeted_refs = {
        ref
        for ref in action_refs
        if ref.startswith(
            (
                "actions/checkout@",
                "actions/setup-node@",
                "actions/cache/restore@",
                "actions/cache/save@",
                "actions/upload-artifact@",
                "astral-sh/setup-uv@",
            )
        )
    }

    assert targeted_refs == expected_refs
    assert combined.count("# v8.3.2") == 4
    assert re.findall(r"node-version:\s+'(\d+)'", combined) == [
        "24",
        "24",
        "24",
        "24",
    ]
    assert "node-version: '22'" not in combined


def test_windows_ci_runs_required_nsis_packaging_regressions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    desktop = workflow.split("desktop-check:", 1)[1]

    assert "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990" in desktop
    assert "uv sync --project apps/sidecar --extra dev --locked" in desktop
    assert "Build NSIS packaging test fixture" in desktop
    assert "Test Windows NSIS packaging policies" in desktop
    assert "CAPTIONNEST_REQUIRE_NSIS_TESTS: '1'" in desktop
    assert "pytest tooling/tests/test_desktop_packaging.py" in desktop


def test_windows_ci_exercises_affected_and_exact_head_installer_lifecycle() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    lifecycle = workflow.split("installer-lifecycle:", 1)[1]
    script = (ROOT / "scripts" / "test-installer-model-retention.ps1").read_text(
        encoding="utf-8"
    )

    assert "timeout-minutes: 180" in lifecycle
    assert "Restore LGPL media wheel cache" in lifecycle
    assert "db4723bd0a99eab031f1a3dee4336dca43049c87" in lifecycle
    assert "install-media-wheel.ps1" in lifecycle
    assert "build-desktop.ps1" in lifecycle
    assert "Build higher-version upgrade fixture from exact HEAD" in lifecycle
    assert "captionnest-upgrade-config.json" in lifecycle
    assert "@{ version = '0.2.9' }" in lifecycle
    assert "'--config', 'apps\\desktop\\tauri.conf.json'" in lifecycle
    assert "'--config', $UpgradeConfig" in lifecycle
    assert "--ignore-version-mismatches" in lifecycle
    assert "CAPTIONNEST_CURRENT_INSTALLER=$ExactCopy" in lifecycle
    assert "CAPTIONNEST_UPGRADE_INSTALLER" in lifecycle
    assert "releases/download/v0.2.8/CaptionNest_0.2.8_x64-setup.exe" in lifecycle
    assert "test-installer-model-retention.ps1" in lifecycle
    assert "-UpgradeInstallerPath $env:CAPTIONNEST_UPGRADE_INSTALLER" in lifecycle
    assert "RUNNER_ENVIRONMENT -ne 'github-hosted'" in script
    assert "Affected installer SHA-256 mismatch" in script
    assert "function Invoke-AffectedUninstallerGuiConfirm" in script
    assert "affected uninstall explicit deletion" in script
    assert "GetLastActivePopup" in script
    assert "EnumerateTopLevelWindows" in script
    assert "BaselineWindowHandles" in script
    assert "ClassName -eq '#32770'" in script
    assert "Multiple CaptionNest GUI windows matched" in script
    assert "No CaptionNest GUI window appeared" in script
    assert "New top-level windows:" in script
    assert "EnumerateChildWindows" in script
    assert "GetDlgCtrlID" in script
    assert "GetWindowLongPtr" in script
    assert "0x00F1" in script
    assert "0x0111" in script
    assert "0x00F5" not in script
    assert "PostMessage" in script
    assert "GetParent" in script
    assert "ControlId -eq $ControlId" in script
    assert "button $ControlId was not enabled" in script
    assert "SendMessageTimeout" in script
    assert "0x0002" in script
    assert "2000" in script
    assert "[CaptionNestNativeMethods]::SendMessage(" not in script
    assert "failed or timed out" in script
    assert "GUI-ACTION:" in script
    assert "upgrade choice transition observed" in script
    assert "ControlId -eq 1201" in script
    assert "upgrade finish retry" in script
    assert "CompletionClickAttempts -lt 2" in script
    assert "did not transition after one re-dispatch" in script
    assert "completion state observed" in script
    assert "function Get-NativeChildDiagnostics" in script
    assert "Native child controls:" in script
    assert "checkbox state was" in script
    assert "UIAutomationClient" not in script
    assert "function Get-CaptionNestInteractiveWindow" in script
    assert "ClassName -eq 'ComboBox'" in script
    assert "ControlId -eq 1002" in script
    assert "language selector default OK" in script
    assert "InitialControls.Count -gt 0" in script
    assert "0x0146" in script
    assert "0x0147" in script
    assert "0x014E" in script
    assert "NSIS language selector did not retain a valid selection" in script
    assert "CaptionNest GUI page did not become ready" in script
    assert "RemainingSelectors.Count -eq 0" in script
    assert "Language selector did not advance" in script
    assert "function Wait-NativeWindowClosed" in script
    assert "Affected uninstaller did not expose its explicit deletion confirmation" in script
    assert "affected uninstall finish" in script
    assert "SawNonReadyFinish" in script
    assert "CompletionTextChanged" in script
    assert "did not reach a distinct enabled completion state" in script
    assert "remained open after its completion button was clicked" in script
    assert "function Remove-OwnedDirectoryWithRetry" in script
    assert "[int]$TimeoutSeconds = 30" in script
    assert "Owned CaptionNest path still exists" in script
    assert "did not exit during cleanup" in script
    for marker in (
        "affected-explicit-uninstall",
        "Test-UpgradeMode -Name 'gui-default'",
        "@('/S')",
        "@('/P')",
        "@('/UPDATE', '/P')",
        "current-uninstall-cancel-keep-delete",
        "Invoke-RestMethod",
        "status -ne 'ready'",
        "ApiProcess.HasExited",
        "last API state",
        "RedirectStandardError",
        "<redacted>",
        "installed sidecar reported retained small model ready",
    ):
        assert marker in script


def test_attestation_is_pinned_minimally_permitted_and_fail_closed() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "contents: write" in workflow
    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert (
        "actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6 # v4.2.0"
        in workflow
    )
    assert "continue-on-error" not in workflow
    assert "attestation-id" in workflow
    assert "attestation-url" in workflow
    assert workflow.index("Install and verify LGPL media wheel") < workflow.index(
        "Test Python with release media runtime"
    ) < workflow.index("Smoke-test the exact installer") < workflow.index(
        "Attest the final Windows installer"
    ) < workflow.index("Create or resume draft, verify assets, and publish")


def test_release_publishes_only_after_exact_draft_asset_digest_verification() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    publish_step = workflow.split(
        "- name: Create or resume draft, verify assets, and publish", 1
    )[1]

    assert "--draft" in publish_step
    assert "Published Release $env:RELEASE_TAG" in publish_step
    assert "--clobber" in publish_step
    assert "--json', 'apiUrl,tagName,isDraft,isPrerelease'" in publish_step
    assert "databaseId" not in publish_step
    assert "$Remote[0].digest -ne $LocalDigest" in publish_step
    assert "@{ draft = $false }" in publish_step
    assert "--method', 'PATCH'" in publish_step
    assert "-not $Published.immutable" in publish_step
    assert publish_step.index("release', 'upload'") < publish_step.index(
        "$Remote[0].digest -ne $LocalDigest"
    ) < publish_step.index("@{ draft = $false }")


def test_tag_move_after_initial_validation_fails_before_publish_patch() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    publish_step = workflow.split(
        "- name: Create or resume draft, verify assets, and publish", 1
    )[1]
    final_gate = publish_step.split(
        "# Re-read both protections immediately before publish.", 1
    )[1].split("$PublishRequest =", 1)[0]

    assert "./scripts/check-release-tag-ruleset.ps1" in final_gate
    assert "'-Phase', 'Immediately before publish'" in final_gate
    assert "'ls-remote', '--exit-code', 'origin'" in final_gate
    assert "$RemotePeeledRef" in final_gate
    assert "$RemoteTagCommit -ne $env:RELEASE_TAG_COMMIT" in final_gate
    assert "$RemoteTagCommit -ne $env:GITHUB_SHA" in final_gate
    assert "Remote tag moved" in final_gate
    assert "--method', 'PATCH'" not in final_gate
    assert publish_step.index("$RemoteTagCommit -ne $env:GITHUB_SHA") < publish_step.index(
        "$PublishRequest ="
    ) < publish_step.index("--method', 'PATCH'")


def test_release_notes_have_verification_commands_and_fixed_safety_text() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "gh attestation verify" in workflow
    assert "gh release verify " in workflow
    assert "gh release verify-asset" in workflow
    assert "SmartScreen" in workflow
    assert "FFMPEG_BUILD_INFO.txt" in workflow


def test_installer_smoke_preserves_existing_installs_and_checks_full_lifecycle() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    smoke = (ROOT / "scripts" / "smoke-test-installer.ps1").read_text(encoding="utf-8")
    smoke_step = workflow.split("- name: Smoke-test the exact installer", 1)[1].split(
        "- name: Upload validated workflow artifact", 1
    )[0]

    assert "Refusing to overwrite an existing installation" in smoke
    assert "CaptionNest contributors" in smoke
    assert "MainWindowTitle -eq 'CaptionNest'" in smoke
    assert "captionnest-sidecar" in smoke
    assert "CloseMainWindow" in smoke
    assert "uninstall.exe" in smoke
    assert "Invoke-HiddenProcessWithTimeout" in smoke
    assert "taskkill.exe" in smoke
    assert "Test-CaptionNestCleanupComplete" in smoke
    assert "Wait-Until -TimeoutSeconds 60" in smoke
    assert "(Get-CaptionNestUninstallEntries).Count -eq 0" in smoke
    assert "registry_paths=" in smoke
    assert "timeout-minutes: 8" in smoke_step


def test_vcpkg_cache_is_saved_before_later_release_checks() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/cache/restore@v6" in workflow
    assert "actions/cache/save@v6" in workflow
    assert workflow.index("Install and verify LGPL media wheel") < workflow.index(
        "Save vcpkg binary cache"
    ) < workflow.index("Lint Python")


def test_release_reuses_a_verified_final_media_wheel_cache() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts" / "install-media-wheel.ps1").read_text(
        encoding="utf-8"
    )
    build_step = workflow.split("- name: Build LGPL media wheel", 1)[1].split(
        "- name: Install and verify LGPL media wheel", 1
    )[0]
    vcpkg_checkout = workflow.split("- name: Check out pinned vcpkg baseline", 1)[
        1
    ].split("- uses: actions/setup-node@v7", 1)[0]

    assert "id: restore_media_wheel_cache" in workflow
    assert "path: tooling/packaging/dist/media-wheel" in workflow
    assert "scripts/build-media-wheel.ps1" in workflow
    assert "tooling/packaging/media-runtime/vcpkg.json" in workflow
    assert "if: steps.restore_media_wheel_cache.outputs.cache-hit != 'true'" in (
        build_step
    )
    assert "if: steps.restore_media_wheel_cache.outputs.cache-hit != 'true'" in (
        vcpkg_checkout
    )
    assert ".\\scripts\\install-media-wheel.ps1" in workflow
    assert workflow.index("Restore LGPL media wheel cache") < workflow.index(
        "Check out pinned vcpkg baseline"
    )
    assert workflow.index("Install and verify LGPL media wheel") < workflow.index(
        "Save LGPL media wheel cache"
    ) < workflow.index("Lint Python")

    assert "MEDIA_WHEEL_PROVENANCE.json" in installer
    assert "Get-FileHash" in installer
    assert "wheel_sha256" in installer
    assert "check-media-license.ps1" in installer
