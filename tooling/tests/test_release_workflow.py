from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
    assert "'-f', \"tag=$env:RELEASE_TAG\"" in prepare
    assert prepare.index("tooling\\release\\version.py") < prepare.index(
        "git @('commit'"
    ) < prepare.index("git @('tag', '-a'") < prepare.index("Dispatch tag-anchored")


def test_release_is_anchored_to_exact_annotated_tag_and_source_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "ref: ${{ inputs.tag }}" in workflow
    assert 'if ($env:GITHUB_REF -ne "refs/tags/$Tag")' in workflow
    assert "$TagType -ne 'tag'" in workflow
    assert 'if ($env:GITHUB_SHA -ne $TagCommit)' in workflow
    assert "check-version-consistency.ps1' '-ExpectedVersion' $Version" in workflow
    assert "Update every project version" not in workflow
    assert "git @('tag'" not in workflow
    assert "git @('push'" not in workflow


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

    assert "actions/cache/restore@v4" in workflow
    assert "actions/cache/save@v4" in workflow
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
    ].split("- uses: actions/setup-node@v4", 1)[0]

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
