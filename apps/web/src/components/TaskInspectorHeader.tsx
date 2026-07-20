import { Clock3, FileVideo2, Languages, Trash2 } from 'lucide-react'

import { formatDateTime, formatRelativeTime } from '../lib/format'
import type { JobView } from '../types/api'

const STATUS_LABELS: Record<JobView['status'], string> = {
  draft: '待启动',
  queued: '排队中',
  running: '处理中',
  waiting_for_input: '等待输入',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

interface TaskInspectorHeaderProps {
  job: JobView
  disabled?: boolean
  onDeleteJob?: () => void
}

export function TaskInspectorHeader({ job, disabled = false, onDeleteJob }: TaskInspectorHeaderProps) {
  const progress = Math.max(0, Math.min(100, job.progress))
  const totalSteps = job.steps.length
  const doneSteps = job.steps.filter((step) => step.status === 'succeeded').length
  const deletable = job.status !== 'queued' && job.status !== 'running'

  return (
    <header className="task-inspector-header">
      <div className="task-inspector-heading">
        <div className="task-inspector-title">
          <FileVideo2 size={17} aria-hidden="true" />
          <div>
            <h2 title={job.source_name}>{job.source_name}</h2>
            <span className={`inspector-status is-${job.status}`}>
              {STATUS_LABELS[job.status]}
              {totalSteps ? ` · ${totalSteps} 步中完成 ${doneSteps} 步` : ''}
            </span>
          </div>
        </div>
        {onDeleteJob && deletable ? (
          <button
            type="button"
            className="delete-job-button"
            disabled={disabled}
            onClick={() => {
              if (window.confirm('将删除任务记录、处理日志与中间产物；源视频和已导出的字幕文件会保留。确定删除这个任务？')) {
                onDeleteJob()
              }
            }}
          >
            <Trash2 size={15} />
            删除任务
          </button>
        ) : null}
      </div>
      <dl className="task-inspector-meta">
        <div>
          <dt><Languages size={13} aria-hidden="true" />目标语言</dt>
          <dd>{job.target_language}</dd>
        </div>
        <div>
          <dt><Clock3 size={13} aria-hidden="true" />更新时间</dt>
          <dd title={formatDateTime(job.updated_at)}>{formatRelativeTime(job.updated_at)}</dd>
        </div>
      </dl>
      <div className="task-inspector-progress">
        <span>总进度</span>
        <strong>{Math.round(progress)}%</strong>
        <i aria-hidden="true"><span style={{ width: `${progress}%` }} /></i>
      </div>
    </header>
  )
}
