import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
LEGACY_ROUTE = (
    ROOT / "tooling" / "tests" / "fixtures" / "release-route-c7066a0.ps1"
)
# Exact c7066a0 route statements after YAML dedent; only trailing blank lines are normalized.
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _extract_route_script(workflow: str) -> str:
    lines = workflow.splitlines()
    step_index = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "- name: Route release request"
    )
    run_index = next(
        index
        for index in range(step_index + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    block: list[str] = []
    for line in lines[run_index + 1 :]:
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= run_indent:
            break
        block.append(line)

    content_indent = min(
        len(line) - len(line.lstrip()) for line in block if line.strip()
    )
    return "\n".join(
        line[content_indent:] if line.strip() else "" for line in block
    ) + "\n"


def _powershell_args(script: Path) -> list[str]:
    assert POWERSHELL is not None, "PowerShell is required by the release workflow"
    args = [POWERSHELL, "-NoProfile"]
    if Path(POWERSHELL).stem.lower() == "powershell":
        args.extend(["-ExecutionPolicy", "Bypass"])
    return [*args, "-File", str(script)]


def _read_powershell_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def _run_route(
    tmp_path: Path,
    script: str,
    *,
    git_ref: str,
    fake_gh: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    output_path = tmp_path / "github-output.txt"
    summary_path = tmp_path / "step-summary.md"
    gh_args_path = tmp_path / "gh-args.txt"
    script_path = tmp_path / "route.ps1"
    prefix = ""
    if fake_gh:
        prefix = """\
function gh {
  $args | Set-Content -LiteralPath $env:FAKE_GH_ARGS -Encoding ascii
  $global:LASTEXITCODE = 0
}
"""
    script_path.write_text(f"{prefix}{script}", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "RELEASE_VERSION_INPUT": "0.2.6",
            "RELEASE_PRERELEASE_INPUT": "false",
            "GITHUB_REF": git_ref,
            "GITHUB_REPOSITORY": "coconilu/captionnest",
            "GITHUB_OUTPUT": str(output_path),
            "GITHUB_STEP_SUMMARY": str(summary_path),
            "FAKE_GH_ARGS": str(gh_args_path),
        }
    )
    completed = subprocess.run(
        _powershell_args(script_path),
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed, output_path, summary_path, gh_args_path


def test_c7066a0_route_script_reproduces_indented_here_string_parser_error(
    tmp_path: Path,
) -> None:
    completed, output_path, _, _ = _run_route(
        tmp_path,
        LEGACY_ROUTE.read_text(encoding="utf-8"),
        git_ref="refs/tags/v0.2.6",
    )

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "White space is not allowed before the string terminator" in output
    assert not output_path.exists()


def test_route_script_executes_tag_build_path_after_yaml_dedent(tmp_path: Path) -> None:
    script = _extract_route_script(RELEASE_WORKFLOW.read_text(encoding="utf-8"))

    completed, output_path, _, _ = _run_route(
        tmp_path,
        script,
        git_ref="refs/tags/v0.2.6",
    )

    assert completed.returncode == 0, completed.stderr
    assert _read_powershell_text(output_path).strip() == "mode=build"


def test_route_script_dispatches_prepare_through_fake_gh(tmp_path: Path) -> None:
    script = _extract_route_script(RELEASE_WORKFLOW.read_text(encoding="utf-8"))

    completed, output_path, summary_path, gh_args_path = _run_route(
        tmp_path,
        script,
        git_ref="refs/heads/main",
        fake_gh=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert _read_powershell_text(output_path).strip() == "mode=prepare"
    assert _read_powershell_text(gh_args_path).splitlines() == [
        "workflow",
        "run",
        "prepare-release.yml",
        "--repo",
        "coconilu/captionnest",
        "--ref",
        "main",
        "-f",
        "version=0.2.6",
        "-f",
        "prerelease=false",
    ]
    summary = _read_powershell_text(summary_path)
    assert summary.splitlines() == [
        "## Prepare Release dispatched",
        "",
        "- Requested tag: `v0.2.6`",
        "- Prerelease: `false`",
        "- This run only routed the request. Prepare Release will create or verify "
        "the annotated tag.",
        "- The tag-anchored Windows build and publish will appear as a separate "
        "Windows Release run.",
    ]
