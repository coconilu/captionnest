from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = ROOT / "apps" / "web" / "src"


def source(path: str) -> str:
    return (WEB_SRC / path).read_text(encoding="utf-8")


def test_app_uses_desktop_tool_shell_and_working_navigation() -> None:
    app = source("App.tsx")
    header = source("components/AppHeader.tsx")
    job_list = source("components/JobListPanel.tsx")
    sidebar = source("components/AppSidebar.tsx")

    assert "<HeroIntro" not in app
    assert "<AppSidebar activeView={activeView} onSelect={setActiveView}" in app
    assert "activeView === 'tasks'" in app
    assert "activeView === 'services'" in app
    assert all(label in sidebar for label in ("任务", "模型与服务", "设置"))
    assert "aria-current={active ? 'page' : undefined}" in sidebar
    assert 'src="/captionnest-logo.svg"' in header
    assert 'src="/favicon.svg"' not in header
    assert '<Plus size={17} aria-hidden="true" />' in job_list
    assert '<FilePlus2 size={16} aria-hidden="true" />' in job_list


def test_task_workspace_uses_table_and_inspector_regions() -> None:
    app = source("App.tsx")
    job_list = source("components/JobListPanel.tsx")

    assert "<TaskInspectorHeader job={detailJob}" in app
    assert 'className="job-table-header"' in job_list
    assert all(
        label in job_list
        for label in ("任务名称", "进度", "当前阶段", "状态", "更新时间")
    )
    assert 'className="job-row-progress-cell"' in job_list


def test_figma_palette_and_desktop_geometry_are_explicit() -> None:
    styles = source("styles.css")

    assert "/* Figma node 30:30 — desktop tool shell */" in styles
    assert "--canvas: #121417;" in styles
    assert "--surface: #181a1f;" in styles
    assert "--accent: #08b7c7;" in styles
    assert "--border: #30343b;" in styles
    assert "height: 56px;" in styles
    assert "grid-template-columns: 184px minmax(0, 1fr);" in styles
    assert "grid-template-columns: minmax(0, 1fr) 400px;" in styles
    assert all(
        token in styles
        for token in (
            "--text-xs: 12px;",
            "--line-xs: 16px;",
            "--text-sm: 14px;",
            "--line-sm: 20px;",
            "--text-md: 18px;",
            "--line-md: 26px;",
            "--text-lg: 24px;",
            "--line-lg: 34px;",
        )
    )
    assert re.search(r"\.app-sidebar button\s*\{[^}]*font-size: var\(--text-sm\);", styles, re.S)
    assert re.search(r"\.job-table-header\s*\{[^}]*font-size: var\(--text-xs\);", styles, re.S)
    assert re.search(r"\.job-row-copy strong\s*\{[^}]*font-size: var\(--text-sm\);", styles, re.S)
    assert re.search(r"\.task-inspector-title h2\s*\{[^}]*font-size: var\(--text-md\);", styles, re.S)
    assert re.search(r"\.pipeline-step-header h3\s*\{[^}]*font-size: var\(--text-sm\);", styles, re.S)
    assert re.search(r"\.create-task-dialog-header h2\s*\{[^}]*font-size: var\(--text-md\);", styles, re.S)
    assert re.search(r"\.batch-creator > footer > p strong\s*\{[^}]*font-size: var\(--text-sm\);", styles, re.S)


def test_legacy_visible_text_selectors_are_overridden_by_readability_tokens() -> None:
    styles = source("styles.css")
    marker = "/* Readability floor for higher-specificity legacy selectors. */"
    floor = styles.split(marker, 1)[1].split(".provider-tabs", 1)[0]

    assert styles.rindex(marker) > styles.rindex("font-size: 7px;")
    assert all(
        selector in floor
        for selector in (
            ".bulk-action-bar > span",
            ".bulk-action-bar button",
            ".console-header > div span",
            ".progress-row strong",
            ".batch-source-empty",
            ".inline-error",
            ".model-status-card > p",
            ".environment-actions button",
        )
    )
    assert "font-size: var(--text-xs);" in floor
    assert "font-size: var(--text-sm);" in floor


def test_figma_modal_and_responsive_breakpoints_keep_actions_reachable() -> None:
    styles = source("styles.css")

    assert "width: min(650px, calc(100vw - 48px));" in styles
    assert "height: min(570px, calc(100dvh - 48px));" in styles
    assert "grid-template-columns: 342px 308px;" in styles
    assert "grid-template-rows: minmax(0, 1fr) 74px;" in styles
    assert "@media (max-width: 920px)" in styles
    assert "@media (max-width: 640px)" in styles
    assert "width: calc(100vw - 16px);" in styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in styles
