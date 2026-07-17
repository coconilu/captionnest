import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FilePlus2,
  Plus,
  Files,
  ListFilter,
  LoaderCircle,
  RefreshCw,
  Search,
} from 'lucide-react'
import { useMemo, useState } from 'react'

import { formatDateTime } from '../lib/format'
import type {
  BatchRecord,
  JobBulkAction,
  JobStatus,
  JobSummaryView,
} from '../types/api'
import { BulkActionBar } from './BulkActionBar'

const STATUS_LABELS: Record<JobStatus, string> = {
  draft: '待启动',
  queued: '排队中',
  running: '处理中',
  waiting_for_input: '等待输入',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

const STEP_LABELS = {
  media: '媒体准备',
  transcription: '语音识别',
  translation: '字幕翻译',
  export: '字幕导出',
} as const

interface JobGroup {
  key: string
  batch: BatchRecord | null
  items: JobSummaryView[]
  latestCreatedAt: string
}

interface JobListPanelProps {
  items: JobSummaryView[]
  batches: BatchRecord[]
  selectedJobId: string | null
  checkedJobIds: Set<string>
  loading: boolean
  error: string | null
  mutationNotice: { message: string; tone: 'success' | 'warning' | 'error' } | null
  bulkBusy: boolean
  onCreateTask: () => void
  onRefresh: () => void
  onSelectJob: (jobId: string) => void
  onToggleJob: (jobId: string, checked: boolean) => void
  onToggleVisible: (jobIds: string[], checked: boolean) => void
  onBulkAction: (action: JobBulkAction) => void
}

function groupJobs(items: JobSummaryView[], batches: BatchRecord[]): JobGroup[] {
  const batchById = new Map(batches.map((batch) => [batch.id, batch]))
  const grouped = new Map<string, JobGroup>()
  items.forEach((item) => {
    const key = item.batch_id ?? 'independent'
    const existing = grouped.get(key)
    if (existing) {
      existing.items.push(item)
      if (item.created_at > existing.latestCreatedAt) existing.latestCreatedAt = item.created_at
      return
    }
    grouped.set(key, {
      key,
      batch: item.batch_id ? batchById.get(item.batch_id) ?? null : null,
      items: [item],
      latestCreatedAt: item.created_at,
    })
  })
  return [...grouped.values()].sort((left, right) =>
    right.latestCreatedAt.localeCompare(left.latestCreatedAt))
}

function groupProgress(group: JobGroup): number {
  if (group.batch) return group.batch.status_summary.progress
  if (!group.items.length) return 0
  return Math.round(group.items.reduce((total, item) => total + item.progress, 0) / group.items.length)
}

function JobRow({
  item,
  selected,
  checked,
  onSelect,
  onToggle,
}: {
  item: JobSummaryView
  selected: boolean
  checked: boolean
  onSelect: () => void
  onToggle: (checked: boolean) => void
}) {
  const step = item.current_step ? STEP_LABELS[item.current_step] : null
  const progress = Math.max(0, Math.min(100, item.progress))

  return (
    <article className={`job-list-row ${selected ? 'is-selected' : ''}`}>
      <label className="job-select-box">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onToggle(event.target.checked)}
          aria-label={`选择任务 ${item.source_name}`}
        />
        <span aria-hidden="true" />
      </label>
      <button
        type="button"
        className="job-row-main"
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
      >
        <span className="job-row-copy">
          <strong title={item.source_name}>{item.source_name}</strong>
          <small>
            {item.source_kind === 'path' ? '本机视频' : '浏览器上传'}
            {item.queue_position ? ` · 队列 #${item.queue_position}` : ''}
          </small>
          {item.error ? <em title={item.error}>{item.error}</em> : null}
        </span>
        <span className="job-row-progress-cell">
          <b>{Math.round(progress)}%</b>
          <i aria-hidden="true"><span style={{ width: `${progress}%` }} /></i>
        </span>
        <span className={`job-row-step ${item.status === 'running' ? 'is-active' : ''}`}>
          {step ?? '等待开始'}
        </span>
        <span className={`job-row-status is-${item.status}`}>
          <i className={`job-status-dot is-${item.status}`} aria-hidden="true" />
          {STATUS_LABELS[item.status]}
        </span>
        <span className="job-row-updated">
          {formatDateTime(item.updated_at)}
          <ChevronRight size={14} aria-hidden="true" />
        </span>
      </button>
    </article>
  )
}

export function JobListPanel({
  items,
  batches,
  selectedJobId,
  checkedJobIds,
  loading,
  error,
  mutationNotice,
  bulkBusy,
  onCreateTask,
  onRefresh,
  onSelectJob,
  onToggleJob,
  onToggleVisible,
  onBulkAction,
}: JobListPanelProps) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<JobStatus | 'all'>('all')
  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
  const visibleItems = useMemo(() => items.filter((item) => {
    if (status !== 'all' && item.status !== status) return false
    return !normalizedQuery || item.source_name.toLocaleLowerCase('zh-CN').includes(normalizedQuery)
  }), [items, normalizedQuery, status])
  const groups = useMemo(() => groupJobs(visibleItems, batches), [batches, visibleItems])
  const visibleIds = useMemo(() => visibleItems.map((item) => item.id), [visibleItems])
  const allVisibleChecked = visibleIds.length > 0
    && visibleIds.every((jobId) => checkedJobIds.has(jobId))

  return (
    <aside className="job-list-panel" aria-labelledby="job-list-title">
      <header className="job-list-heading">
        <div>
          <h2 id="job-list-title">字幕任务</h2>
          <p>管理识别、翻译与字幕导出 · {items.length} 个任务</p>
        </div>
        <div>
          <button type="button" className="icon-button job-refresh-button" onClick={onRefresh} aria-label="刷新任务列表">
            <RefreshCw size={17} className={loading ? 'is-spinning' : undefined} />
          </button>
          <button
            type="button"
            className="button button-secondary add-batch-button"
            data-create-task-trigger="toolbar"
            onClick={onCreateTask}
          >
            <Plus size={17} aria-hidden="true" />
            新建任务
          </button>
        </div>
      </header>

      <div className="job-list-controls">
        <div className="job-list-filters">
        <label>
          <Search size={15} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件名"
            aria-label="搜索任务文件名"
          />
        </label>
        <label>
          <ListFilter size={15} aria-hidden="true" />
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as JobStatus | 'all')}
            aria-label="按任务状态筛选"
          >
            <option value="all">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        </div>

        <div className="job-selection-row">
          <label>
            <input
              type="checkbox"
              checked={allVisibleChecked}
              onChange={(event) => onToggleVisible(visibleIds, event.target.checked)}
              disabled={!visibleIds.length}
            />
            <span>选择当前 {visibleIds.length} 项</span>
          </label>
          {loading ? <span><LoaderCircle size={13} className="is-spinning" />同步中</span> : null}
        </div>
      </div>

      <BulkActionBar
        selectedCount={checkedJobIds.size}
        busy={bulkBusy}
        onAction={onBulkAction}
      />

      {error ? (
        <div className="inline-error job-list-message" role="alert">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      ) : null}
      {mutationNotice ? (
        <div className={`job-list-message is-${mutationNotice.tone}`} role="status">
          {mutationNotice.tone === 'success'
            ? <CheckCircle2 size={16} />
            : <AlertCircle size={16} />}
          <span>{mutationNotice.message}</span>
        </div>
      ) : null}

      <div className="job-table">
        <div className="job-table-header" aria-hidden="true">
          <span>任务名称</span>
          <span>进度</span>
          <span>当前阶段</span>
          <span>状态</span>
          <span>更新时间</span>
        </div>
        <div className="job-groups">
          {groups.map((group) => {
          const progress = groupProgress(group)
          const name = group.batch?.name
            ?? (group.key === 'independent' ? '独立任务' : `批次 ${group.key.slice(0, 8)}`)
            return (
              <details key={group.key} className="job-batch-group" open>
              <summary>
                <span className="job-batch-icon"><Files size={15} /></span>
                <span>
                  <strong>{name}</strong>
                  <small>{group.items.length} 项 · {progress}%</small>
                </span>
                <i aria-hidden="true"><span style={{ width: `${progress}%` }} /></i>
              </summary>
              <div>
                {group.items.map((item) => (
                  <JobRow
                    key={item.id}
                    item={item}
                    selected={selectedJobId === item.id}
                    checked={checkedJobIds.has(item.id)}
                    onSelect={() => onSelectJob(item.id)}
                    onToggle={(checked) => onToggleJob(item.id, checked)}
                  />
                ))}
              </div>
              </details>
            )
          })}

          {!groups.length && !loading ? (
            <div className="job-list-empty">
              {items.length ? <Search size={22} /> : <Clock3 size={22} />}
              <strong>{items.length ? '没有匹配的任务' : '还没有任务'}</strong>
              <span>{items.length ? '调整搜索或状态筛选' : '添加视频来创建第一个任务'}</span>
              {!items.length ? (
                <button type="button" className="button button-secondary empty-create-task-button" onClick={onCreateTask}>
                  <FilePlus2 size={16} aria-hidden="true" />
                  新建任务
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </aside>
  )
}
