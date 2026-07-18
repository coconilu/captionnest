from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = ROOT / "apps" / "web" / "src"
SIDECAR_SRC = ROOT / "apps" / "sidecar" / "src" / "sublingo_local"


def source(path: str) -> str:
    return (WEB_SRC / path).read_text(encoding="utf-8")


def test_sidecar_version_comes_from_packaged_project_metadata() -> None:
    package_init = (SIDECAR_SRC / "__init__.py").read_text(encoding="utf-8")
    packaging_spec = (ROOT / "tooling" / "packaging" / "captionnest-sidecar.spec").read_text(
        encoding="utf-8"
    )

    assert '__version__ = distribution_version("captionnest")' in package_init
    assert '__version__ = "' not in package_init
    assert 'copy_metadata("captionnest")' in packaging_spec


def test_health_version_reaches_the_settings_about_panel() -> None:
    api_types = source("types/api.ts")
    backend_hook = source("hooks/useBackendStatus.ts")
    app = source("App.tsx")

    assert "version?: string" in api_types
    assert "health: BackendHealth | null" in backend_hook
    assert "health: health.value" in backend_hook
    assert "sidecarVersion={backendHealth?.version ?? null}" in app
    assert "<AboutPanel" in app


def test_browser_guard_precedes_the_tauri_version_call() -> None:
    version_source = source("lib/appVersion.ts")
    browser_guard = version_source.index("if (!runtime.isDesktop())")
    runtime_call = version_source.index("await runtime.getDesktopVersion()")

    assert browser_guard < runtime_call
    assert "await import('@tauri-apps/api/app')" in version_source
    assert "return { status: 'browser', version: null }" in version_source


def test_about_panel_is_accessible_and_responsive() -> None:
    panel = source("components/AboutPanel.tsx")
    styles = source("styles.css")

    assert 'aria-labelledby="about-panel-title"' in panel
    assert 'aria-live="polite"' in panel
    assert "role={display.noticeTone === 'warning' ? 'alert' : 'status'}" in panel
    assert "overflow-wrap: anywhere;" in styles
    assert ".about-version-row" in styles
    assert "@media (max-width: 640px)" in styles


def test_version_logic_has_explicit_mismatch_and_fallback_copy() -> None:
    version_source = source("lib/appVersion.ts")

    assert "检测到组件版本不一致" in version_source
    assert "桌面应用 ${versionLabel(desktop.version)}" in version_source
    assert "Sidecar ${versionLabel(cleanSidecarVersion)}" in version_source
    assert "浏览器开发模式" in version_source
    assert "版本读取失败" in version_source
    assert "未连接" in version_source
