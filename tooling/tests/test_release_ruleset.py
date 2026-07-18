import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-release-tag-ruleset.ps1"


def _base_ruleset() -> dict[str, object]:
    return {
        "id": 19140023,
        "target": "tag",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["refs/tags/v*"],
                "exclude": [],
            }
        },
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }


def _run_ruleset_check(
    tmp_path: Path, ruleset: dict[str, object]
) -> tuple[subprocess.CompletedProcess[str], str]:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh is not None, "PowerShell is required to validate release workflow scripts"
    fixture = tmp_path / "ruleset.json"
    summary = tmp_path / "summary.md"
    fixture.write_text(json.dumps(ruleset), encoding="utf-8")
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-RulesetDetailsPath",
            str(fixture),
            "-SummaryPath",
            str(summary),
            "-Phase",
            "Test fixture",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    summary_text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return result, summary_text


def test_missing_admin_only_fields_pass_with_explicit_external_prerequisite(
    tmp_path: Path,
) -> None:
    result, summary = _run_ruleset_check(tmp_path, _base_ruleset())

    assert result.returncode == 0, result.stderr
    output = " ".join((result.stdout + result.stderr).split())
    assert "external administrator prerequisite" in output
    assert "was not verified by this workflow" in output
    assert "external administrator prerequisite" in summary
    assert "was not verified by this workflow" in summary


def test_visible_safe_admin_fields_are_verified(tmp_path: Path) -> None:
    ruleset = _base_ruleset()
    ruleset["bypass_actors"] = []
    ruleset["current_user_can_bypass"] = "never"

    result, summary = _run_ruleset_check(tmp_path, ruleset)

    assert result.returncode == 0, result.stderr
    assert "No-bypass fields were visible and verified" in result.stdout
    assert "No-bypass fields were visible and verified" in summary


@pytest.mark.parametrize(
    ("bypass_actors", "current_user_can_bypass"),
    [
        ([{"actor_type": "RepositoryRole", "actor_id": 5}], "never"),
        (None, "never"),
        ([], "always"),
    ],
)
def test_visible_bypass_state_fails_closed(
    tmp_path: Path,
    bypass_actors: list[dict[str, object]] | None,
    current_user_can_bypass: str,
) -> None:
    ruleset = _base_ruleset()
    ruleset["bypass_actors"] = bypass_actors
    ruleset["current_user_can_bypass"] = current_user_can_bypass

    result, summary = _run_ruleset_check(tmp_path, ruleset)

    assert result.returncode != 0
    assert "RULESET_PROTECTION_MISSING" in result.stderr
    assert summary == ""


@pytest.mark.parametrize(
    "visible_admin_field",
    [
        {"bypass_actors": []},
        {"current_user_can_bypass": "never"},
    ],
)
def test_partial_admin_visibility_fails_closed(
    tmp_path: Path, visible_admin_field: dict[str, object]
) -> None:
    ruleset = _base_ruleset()
    ruleset.update(visible_admin_field)

    result, summary = _run_ruleset_check(tmp_path, ruleset)

    assert result.returncode != 0
    assert "RULESET_ADMIN_VISIBILITY_PARTIAL" in result.stderr
    assert summary == ""


@pytest.mark.parametrize(
    "mutation",
    [
        {"target": "branch"},
        {"enforcement": "disabled"},
        {"conditions": {"ref_name": {"include": ["refs/tags/*"], "exclude": []}}},
        {
            "conditions": {
                "ref_name": {
                    "include": ["refs/tags/v*"],
                    "exclude": ["refs/tags/v0.2.2"],
                }
            }
        },
        {"rules": [{"type": "update"}]},
        {"rules": [{"type": "deletion"}]},
    ],
)
def test_structural_release_tag_protection_fails_closed(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    ruleset = _base_ruleset()
    ruleset.update(mutation)

    result, summary = _run_ruleset_check(tmp_path, ruleset)

    assert result.returncode != 0
    assert "RULESET_PROTECTION_MISSING" in result.stderr
    assert summary == ""
