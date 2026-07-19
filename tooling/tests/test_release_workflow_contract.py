import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CHECK = ROOT / "scripts" / "check-release-workflow-contract.ps1"
CURRENT_RELEASE_WORKFLOW = (
    ROOT / ".github" / "workflows" / "release.yml"
).read_text(encoding="utf-8")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
ANSI_CONTROL_SEQUENCE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
POWERSHELL_RICH_ERROR_CONTINUATION_GUTTER = re.compile(
    r"(?m)^ {5}\| +(?![~^])(?=\S)"
)
FAIL_CLOSED_MESSAGE = (
    "uses a legacy or unknown immutable release workflow contract and cannot be "
    "rerun safely. Keep the tag unchanged and publish a new version."
)

LEGACY_RELEASE_WORKFLOW = """\
name: Legacy Windows Release
on:
  workflow_dispatch:
    inputs:
      tag:
        required: true
        type: string
jobs: {}
"""

UNKNOWN_RELEASE_WORKFLOW = """\
name: Unknown Windows Release
on:
  workflow_dispatch:
    inputs:
      version:
        required: true
        type: string
jobs: {}
"""


def _git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _write_workflow(repo: Path, workflow: str) -> None:
    path = repo / ".github" / "workflows" / "release.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(workflow, encoding="utf-8")


def _commit_workflow(repo: Path, workflow: str, message: str) -> None:
    _write_workflow(repo, workflow)
    _git(repo, "add", ".github/workflows/release.yml")
    _git(repo, "commit", "-m", message)


def _init_contract_repo(tmp_path: Path, tag: str, tag_workflow: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "CaptionNest tests")
    _git(repo, "config", "user.email", "captionnest-tests@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")
    _commit_workflow(repo, tag_workflow, "tag workflow")
    _git(repo, "tag", "-a", tag, "-m", tag)
    if tag_workflow != CURRENT_RELEASE_WORKFLOW:
        _commit_workflow(repo, CURRENT_RELEASE_WORKFLOW, "current workflow")
    return repo


def _check_contract(repo: Path, tag: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None, "PowerShell is required by the release workflow"
    shell_args = [POWERSHELL, "-NoProfile"]
    if Path(POWERSHELL).stem.lower() == "powershell":
        shell_args.extend(["-ExecutionPolicy", "Bypass"])
    return subprocess.run(
        shell_args
        + [
            "-File",
            str(CONTRACT_CHECK),
            "-Tag",
            tag,
            "-ContractRef",
            "HEAD",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def _normalize_powershell_output(completed: subprocess.CompletedProcess[str]) -> str:
    output = f"{completed.stdout}\n{completed.stderr}"
    without_ansi = ANSI_CONTROL_SEQUENCE.sub("", output)
    without_continuation_gutters = POWERSHELL_RICH_ERROR_CONTINUATION_GUTTER.sub(
        "", without_ansi
    )
    return " ".join(without_continuation_gutters.split())


def test_current_tag_workflow_contract_is_dispatchable(tmp_path: Path) -> None:
    repo = _init_contract_repo(tmp_path, "v0.2.5", CURRENT_RELEASE_WORKFLOW)

    completed = _check_contract(repo, "v0.2.5")

    assert completed.returncode == 0, completed.stderr
    assert "uses the current immutable release workflow contract" in completed.stdout


@pytest.mark.parametrize(
    ("tag", "tag_workflow"),
    [
        ("v0.2.2", LEGACY_RELEASE_WORKFLOW),
        ("v0.2.4", UNKNOWN_RELEASE_WORKFLOW),
    ],
)
def test_legacy_or_unknown_tag_workflow_schema_fails_closed(
    tmp_path: Path, tag: str, tag_workflow: str
) -> None:
    repo = _init_contract_repo(tmp_path, tag, tag_workflow)

    completed = _check_contract(repo, tag)
    output = _normalize_powershell_output(completed)

    assert completed.returncode != 0
    assert f"{tag} {FAIL_CLOSED_MESSAGE}" in output
    assert "legacy or unknown immutable release workflow contract" in output
    assert "cannot be rerun safely" in output
    assert "Keep the tag unchanged and publish a new version" in output


def test_same_inputs_with_different_tag_workflow_fails_closed(tmp_path: Path) -> None:
    altered_workflow = CURRENT_RELEASE_WORKFLOW.replace(
        "if ($env:GITHUB_SHA -ne $TagCommit)",
        "if ($env:GITHUB_SHA -eq '')",
        1,
    )
    assert altered_workflow != CURRENT_RELEASE_WORKFLOW
    repo = _init_contract_repo(tmp_path, "v0.2.6", altered_workflow)

    completed = _check_contract(repo, "v0.2.6")
    output = _normalize_powershell_output(completed)

    assert completed.returncode != 0
    assert f"v0.2.6 {FAIL_CLOSED_MESSAGE}" in output
    assert "legacy or unknown immutable release workflow contract" in output


def test_powershell_output_normalization_preserves_complete_fail_closed_message() -> None:
    raw_output = (
        "\x1b[31;1mLine |\x1b[0m\r\n"
        "  86 |  \x1b[31;1mthrow \"release workflow contract rejected\"\x1b[0m\r\n"
        "     |  \x1b[31;1m~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\x1b[0m\r\n"
        "\x1b[31;1m     | \x1b[0m"
        "\x1b[31;1mv0.2.4 uses a legacy or unknown immutable release workflow\x1b[0m\r\n"
        "\x1b[31;1m     | \x1b[0m"
        "\x1b[31;1mcontract and cannot be rerun safely. Keep the tag unchanged\x1b[0m\r\n"
        "\x1b[31;1m     | \x1b[0m"
        "\x1b[31;1mand publish a new version.\x1b[0m"
    )
    completed = subprocess.CompletedProcess(
        args=["pwsh", "-File", str(CONTRACT_CHECK)],
        returncode=1,
        stdout="",
        stderr=raw_output,
    )
    ansi_and_whitespace_only = " ".join(
        ANSI_CONTROL_SEQUENCE.sub("", raw_output).split()
    )
    normalized_output = _normalize_powershell_output(completed)

    assert completed.returncode != 0
    assert f"v0.2.4 {FAIL_CLOSED_MESSAGE}" not in raw_output
    assert f"v0.2.4 {FAIL_CLOSED_MESSAGE}" not in ansi_and_whitespace_only
    assert f"v0.2.4 {FAIL_CLOSED_MESSAGE}" in normalized_output
    assert "Line |" in normalized_output
    assert '86 | throw "release workflow contract rejected"' in normalized_output
    assert "| ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" in normalized_output
