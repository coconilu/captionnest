from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
        "Build and install LGPL media wheel",
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
    smoke = (ROOT / "scripts" / "smoke-test-installer.ps1").read_text(encoding="utf-8")

    assert "Refusing to overwrite an existing installation" in smoke
    assert "CaptionNest contributors" in smoke
    assert "MainWindowTitle -eq 'CaptionNest'" in smoke
    assert "captionnest-sidecar" in smoke
    assert "CloseMainWindow" in smoke
    assert "uninstall.exe" in smoke
