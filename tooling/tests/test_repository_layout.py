from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_projects_and_tooling_have_explicit_roots() -> None:
    expected = (
        ROOT / "apps" / "sidecar" / "pyproject.toml",
        ROOT / "apps" / "sidecar" / "src" / "sublingo_local" / "app.py",
        ROOT / "apps" / "web" / "package.json",
        ROOT / "apps" / "desktop" / "Cargo.toml",
        ROOT / "tooling" / "packaging" / "captionnest-sidecar.spec",
        ROOT / "tooling" / "release" / "version.py",
    )
    assert all(path.is_file() for path in expected)

    legacy_entries = (
        ROOT / "pyproject.toml",
        ROOT / "src" / "sublingo_local",
        ROOT / "tests" / "test_api.py",
        ROOT / "web" / "package.json",
        ROOT / "src-tauri" / "Cargo.toml",
        ROOT / "packaging" / "captionnest-sidecar.spec",
    )
    assert all(not path.exists() for path in legacy_entries)


def test_sidecar_distribution_copies_match_repository_license_sources() -> None:
    sidecar = ROOT / "apps" / "sidecar"

    assert (sidecar / "LICENSE").read_text(encoding="utf-8").rstrip().splitlines() == (
        ROOT / "LICENSE"
    ).read_text(encoding="utf-8").rstrip().splitlines()
    assert (sidecar / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    ).rstrip().splitlines() == (ROOT / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    ).rstrip().splitlines()


def test_tauri_paths_join_the_new_app_and_tooling_boundaries() -> None:
    desktop = ROOT / "apps" / "desktop"
    config = json.loads((desktop / "tauri.conf.json").read_text(encoding="utf-8"))

    assert (desktop / config["build"]["frontendDist"]).resolve() == (
        ROOT / "apps" / "web" / "dist"
    ).resolve()

    resources = config["bundle"]["resources"]
    expected_sources = {
        (desktop / "binaries" / "_internal").resolve(),
        (ROOT / "LICENSE").resolve(),
        (ROOT / "THIRD_PARTY_NOTICES.md").resolve(),
        (ROOT / "licenses").resolve(),
        (ROOT / "tooling" / "packaging" / "dist" / "FFMPEG_BUILD_INFO.txt").resolve(),
    }
    actual_sources = {(desktop / source).resolve() for source in resources}
    assert actual_sources == expected_sources


def test_local_dev_ports_are_fixed_and_aligned() -> None:
    desktop_config = json.loads(
        (ROOT / "apps" / "desktop" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    package = json.loads(
        (ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
    )
    vite_config = (ROOT / "apps" / "web" / "vite.config.ts").read_text(encoding="utf-8")
    dev_script = (ROOT / "scripts" / "dev.ps1").read_text(encoding="utf-8")
    serve_script = (ROOT / "scripts" / "serve.ps1").read_text(encoding="utf-8")
    stop_script = (ROOT / "scripts" / "stop-local-services.ps1").read_text(
        encoding="utf-8"
    )

    assert desktop_config["build"]["devUrl"] == "http://127.0.0.1:5175"
    assert "host: '127.0.0.1'" in vite_config
    assert "port: 5175" in vite_config
    assert "strictPort: true" in vite_config
    assert "--port 5175 --strictPort" in dev_script
    assert "$Ports += 5175" in stop_script
    assert "stop-local-services.ps1 -Scope Web" in package["scripts"]["predev"]
    assert "npm --prefix apps/web run build" in serve_script
    assert "stop-local-services.ps1') -Scope All" in serve_script
    assert "--host', '127.0.0.1', '--port', '8765'" in serve_script
    assert "--reload" not in serve_script


def test_pyinstaller_spec_uses_repository_level_roots() -> None:
    spec = (ROOT / "tooling" / "packaging" / "captionnest-sidecar.spec").read_text(
        encoding="utf-8"
    )

    assert 'SIDECAR_ROOT = ROOT / "apps" / "sidecar"' in spec
    assert 'PACKAGING_ROOT = ROOT / "tooling" / "packaging"' in spec
    assert 'DESKTOP_ROOT = ROOT / "apps" / "desktop"' in spec
    assert 'pathex=[str(SIDECAR_ROOT / "src")]' in spec


def test_frontend_ci_and_review_commands_run_the_web_test_suite() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    frontend_job = workflow.split("  frontend:", maxsplit=1)[1].split(
        "  desktop-check:", maxsplit=1
    )[0]
    review_guide = (ROOT / "docs" / "code-review.md").read_text(encoding="utf-8")

    expected_steps = (
        "- run: npm ci\n        working-directory: apps/web",
        "- run: npm test\n        working-directory: apps/web",
        "- run: npm run lint\n        working-directory: apps/web",
        "- run: npm run build\n        working-directory: apps/web",
    )
    assert all(step in frontend_job for step in expected_steps)
    assert [frontend_job.index(step) for step in expected_steps] == sorted(
        frontend_job.index(step) for step in expected_steps
    )
    assert "Set-Location apps/web\nnpm run lint\nnpm test\nnpm run build" in review_guide
