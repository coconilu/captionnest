from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = ROOT / "apps" / "web" / "src"


def source(path: str) -> str:
    return (WEB_SRC / path).read_text(encoding="utf-8")


def test_task_workspace_is_default_and_both_entry_points_share_the_dialog() -> None:
    app = source("App.tsx")
    job_list = source("components/JobListPanel.tsx")

    assert "const [creatorOpen, setCreatorOpen] = useState(false)" in app
    assert '<CreateTaskDialog\n        open={creatorOpen}' in app
    assert "batch-creator-layout" not in app
    assert "onCreateTask={() => setCreatorOpen(true)}" in app
    assert job_list.count("onClick={onCreateTask}") == 2
    assert job_list.count("新建任务") >= 2
    assert "empty-create-task-button" in job_list


def test_dialog_owns_modal_semantics_focus_trap_and_background_lock() -> None:
    dialog = source("components/CreateTaskDialog.tsx")

    required_contract = (
        'role="dialog"',
        'aria-modal="true"',
        'aria-labelledby="create-task-dialog-title"',
        "appShell?.setAttribute('inert', '')",
        "body.style.overflow = 'hidden'",
        "event.key === 'Escape'",
        "event.key !== 'Tab'",
        "returnFocusRef.current?.isConnected",
        'data-create-task-trigger="toolbar"',
        "if (!busy)",
    )
    assert all(contract in dialog for contract in required_contract)
    assert "event.target === event.currentTarget" in dialog
    assert "onRequestClose()" in dialog

    job_list = source("components/JobListPanel.tsx")
    assert 'data-create-task-trigger="toolbar"' in job_list


def test_batch_creator_keeps_existing_file_and_submit_capabilities_in_dialog() -> None:
    creator = source("components/BatchCreator.tsx")

    assert "pickVideos()" in creator
    assert "uploadFiles(files)" in creator
    assert "preflightBatch" in creator
    assert "createBatch" in creator
    assert "仅创建" in creator
    assert "创建并启动" in creator
    assert "failedBySourceId" in creator
    assert '<div className="batch-creator-settings">{children}</div>' in creator
    assert "取消" in creator


def test_dialog_has_required_responsive_shell_and_fixed_action_area() -> None:
    styles = source("styles.css")

    assert ".create-task-dialog-layer" in styles
    assert ".create-task-dialog" in styles
    assert "grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr);" in styles
    assert "@media (max-width: 920px)" in styles
    assert "@media (max-width: 640px)" in styles
    assert "height: calc(100dvh - 16px);" in styles
    assert "position: sticky;" in styles
    assert "overflow-y: auto;" in styles


def test_900_by_500_dialog_constrains_height_and_keeps_footer_actions() -> None:
    styles = source("styles.css")
    creator = source("components/BatchCreator.tsx")
    narrow_styles = styles.split("@media (max-width: 920px)", maxsplit=1)[1]
    viewport_height = 500
    dialog_height = viewport_height - 32
    constrained_min_height = min(540, viewport_height - 48)
    resolved_dialog_height = max(dialog_height, constrained_min_height)
    dialog_top = (viewport_height - resolved_dialog_height) / 2
    dialog_bottom = dialog_top + resolved_dialog_height
    footer_bottom = dialog_bottom

    assert "min-height: min(540px, calc(100dvh - 48px));" in styles
    assert "height: calc(100dvh - 32px);" in narrow_styles
    assert "overflow-y: auto;" in narrow_styles
    assert "position: sticky;" in narrow_styles
    assert dialog_top >= 0
    assert dialog_bottom <= viewport_height
    assert footer_bottom <= viewport_height
    assert 'onClick={onClose}' in creator
    assert 'onClick={() => void create(false)}' in creator
    assert 'onClick={() => void create(true)}' in creator
    assert all(label in creator for label in ("取消", "仅创建", "创建并启动"))
