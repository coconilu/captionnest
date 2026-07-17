import { Clock3, FileVideo2, Languages } from 'lucide-react'

import { formatDateTime } from '../lib/format'
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

export function TaskInspectorHeader({ job }: { job: JobView }) {
  const progress = Math.max(0, Math.min(100, job.progress))

  return (
    <header className="task-inspector-header">
      <div className="task-inspector-title">
        <FileVideo2 size={17} aria-hidden="true" />
        <div>
          <h2 title={job.source_name}>{job.source_name}</h2>
          <span className={`inspector-status is-${job.status}`}>{STATUS_LABELS[job.status]}</span>
        </div>
      </div>
      <dl className="task-inspector-meta">
        <div>
          <dt><Languages size={13} aria-hidden="true" />目标语言</dt>
          <dd>{job.target_language}</dd>
        </div>
        <div>
          <dt><Clock3 size={13} aria-hidden="true" />更新时间</dt>
          <dd>{formatDateTime(job.updated_at)}</dd>
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
