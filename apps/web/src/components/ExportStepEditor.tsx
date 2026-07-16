import { useState } from 'react'

import type { ExportStepConfig } from '../types/api'

interface ExportStepEditorProps {
  value: ExportStepConfig
  saving: boolean
  onCancel: () => void
  onSave: (value: ExportStepConfig) => void
}

export function ExportStepEditor({
  value,
  saving,
  onCancel,
  onSave,
}: ExportStepEditorProps) {
  const [draft, setDraft] = useState(value)

  return (
    <form
      className="pipeline-editor-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSave({
          ...draft,
          output_directory: draft.output_directory?.trim() || null,
        })
      }}
    >
      <label className="field">
        <span>输出目录</span>
        <input
          value={draft.output_directory ?? ''}
          disabled={saving}
          spellCheck={false}
          placeholder="留空则输出到源视频目录"
          onChange={(event) => setDraft((current) => ({
            ...current,
            output_directory: event.target.value || null,
          }))}
        />
      </label>
      <label className="switch-row">
        <span>
          <strong>覆盖同名字幕</strong>
          <small>输出保持为 &lt;视频名&gt;.srt</small>
        </span>
        <input
          type="checkbox"
          checked={draft.overwrite_existing}
          disabled={saving}
          onChange={(event) => setDraft((current) => ({
            ...current,
            overwrite_existing: event.target.checked,
          }))}
        />
        <i aria-hidden="true" />
      </label>
      <div className="pipeline-editor-actions">
        <button type="button" onClick={onCancel} disabled={saving}>取消</button>
        <button type="submit" className="button-primary" disabled={saving}>
          {saving ? '保存中…' : '保存导出配置'}
        </button>
      </div>
    </form>
  )
}
