from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_workflow_is_one_click_and_main_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "version:" in workflow
    assert "prerelease:" in workflow
    assert "ref: main" in workflow
    assert "publish_release" not in workflow
    assert "tags:" not in workflow


def test_release_workflow_owns_the_complete_release_transaction() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    expected_steps = (
        "Update every project version",
        "Build LGPL media wheel",
        "Install and verify LGPL media wheel",
        "Test Python with release media runtime",
        "Build NSIS and checksum",
        "Smoke-test the exact installer",
        "Commit the version and create the tag",
        "Publish GitHub Release with generated notes",
    )
    for step in expected_steps:
        assert step in workflow

    assert "generate_release_notes: true" in workflow
    assert "git push origin HEAD:main" in workflow
    assert 'git push origin "refs/tags/$env:RELEASE_TAG"' in workflow


def test_release_validation_does_not_fail_when_release_and_tag_are_missing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "gh release view" not in workflow
    assert "git show-ref --verify" not in workflow
    assert "gh release list" in workflow
    assert "git tag --list" in workflow
    assert "$ReleaseExists = @($Releases).tagName -contains $Tag" in workflow
    assert "$TagExists = $MatchingTags -contains $Tag" in workflow


def test_generated_release_notes_have_chinese_categories_and_fixed_safety_text() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    notes_config = (ROOT / ".github" / "release.yml").read_text(encoding="utf-8")

    assert "新功能" in notes_config
    assert "问题修复" in notes_config
    assert "其他变更" in notes_config
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
