import { Ban, Play, RotateCcw, Trash2 } from 'lucide-react'

import type { JobBulkAction } from '../types/api'

interface BulkActionBarProps {
  selectedCount: number
  busy: boolean
  onAction: (action: JobBulkAction) => void
}

export function BulkActionBar({ selectedCount, busy, onAction }: BulkActionBarProps) {
  const disabled = busy || selectedCount === 0

  return (
    <div className="bulk-action-bar" aria-label="批量任务操作">
      <span>已选 {selectedCount} 项</span>
      <div>
        <button type="button" disabled={disabled} onClick={() => onAction('run')}>
          <Play size={14} fill="currentColor" aria-hidden="true" />
          启动
        </button>
        <button type="button" disabled={disabled} onClick={() => onAction('cancel')}>
          <Ban size={14} aria-hidden="true" />
          取消
        </button>
        <button type="button" disabled={disabled} onClick={() => onAction('retry_failed')}>
          <RotateCcw size={14} aria-hidden="true" />
          重试失败
        </button>
        <button
          type="button"
          className="is-danger"
          disabled={disabled}
          onClick={() => {
            if (window.confirm(`删除选中的 ${selectedCount} 个任务及其中间产物？已导出的 SRT 不会删除。`)) {
              onAction('delete')
            }
          }}
        >
          <Trash2 size={14} aria-hidden="true" />
          删除
        </button>
      </div>
    </div>
  )
}
