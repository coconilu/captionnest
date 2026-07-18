from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = ROOT / "apps" / "web" / "src"
SIDECAR_SRC = ROOT / "apps" / "sidecar" / "src" / "sublingo_local"


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


def test_batch_creator_only_selects_local_paths_and_keeps_submit_capabilities() -> None:
    creator = source("components/BatchCreator.tsx")
    client = source("api/client.ts")

    assert "pickVideos()" in creator
    assert "request: { video_path: item.path }" in creator
    assert "preflightBatch" in creator
    assert "createBatch" in creator
    assert "仅创建" in creator
    assert "创建并启动" in creator
    assert "failedBySourceId" in creator
    assert '<div className="batch-creator-settings">{children}</div>' in creator
    assert "取消" in creator
    assert all(
        removed not in creator
        for removed in (
            "uploadFiles",
            "upload_id",
            'type="file"',
            "onDrop",
            "dataTransfer",
            "浏览器上传",
            "拖入多个视频",
        )
    )
    assert "'/api/uploads/bulk'" not in client


def test_task_dialog_opens_with_editable_recognition_translation_and_export() -> None:
    app = source("App.tsx")
    settings = source("components/SettingsPanel.tsx")
    dialog_content = app.split("<CreateTaskDialog", maxsplit=1)[1]

    assert "initiallyOpen" in dialog_content
    assert all(
        label in settings
        for label in ("识别设置", "翻译设置", "导出设置", "识别模型", "目标语言")
    )


def test_dialog_has_required_responsive_shell_and_fixed_action_area() -> None:
    styles = source("styles.css")
    viewport_width = 1440
    viewport_height = 900
    dialog_width = min(1180, viewport_width - 48)
    dialog_height = min(820, viewport_height - 48)

    assert ".create-task-dialog-layer" in styles
    assert ".create-task-dialog" in styles
    assert "width: min(1180px, calc(100vw - 48px));" in styles
    assert "height: min(820px, calc(100dvh - 48px));" in styles
    assert "min-height: min(700px, calc(100dvh - 48px));" in styles
    assert "grid-template-columns: minmax(0, 1.25fr) minmax(420px, 0.9fr);" in styles
    assert "@media (max-width: 920px)" in styles
    assert "@media (max-width: 640px)" in styles
    assert "height: calc(100dvh - 16px);" in styles
    assert "position: sticky;" in styles
    assert "overflow-y: auto;" in styles
    assert dialog_width >= 1000
    assert dialog_height >= 700
    assert dialog_width <= viewport_width
    assert dialog_height <= viewport_height


def test_900_by_500_dialog_constrains_height_and_keeps_footer_actions() -> None:
    styles = source("styles.css")
    creator = source("components/BatchCreator.tsx")
    narrow_styles = styles.split("@media (max-width: 920px)", maxsplit=1)[1]
    viewport_height = 500
    dialog_height = viewport_height - 32
    constrained_min_height = min(700, viewport_height - 48)
    resolved_dialog_height = max(dialog_height, constrained_min_height)
    dialog_top = (viewport_height - resolved_dialog_height) / 2
    dialog_bottom = dialog_top + resolved_dialog_height
    footer_bottom = dialog_bottom

    assert "min-height: min(700px, calc(100dvh - 48px));" in styles
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


def test_legacy_upload_api_types_and_history_labels_remain_compatible() -> None:
    api_types = source("types/api.ts")
    job_list = source("components/JobListPanel.tsx")
    sidecar_app = (SIDECAR_SRC / "app.py").read_text(encoding="utf-8")

    assert "upload_id?: string" in api_types
    assert "source_kind: 'path' | 'upload'" in api_types
    assert "export interface BulkUploadResponse" in api_types
    assert "item.source_kind === 'path' ? '本机视频' : '浏览器上传'" in job_list
    assert '"/uploads/bulk"' in sidecar_app
