import {
  AlertCircle,
  Check,
  Clock3,
  FileOutput,
  History,
  LoaderCircle,
  Pencil,
  Play,
  RefreshCw,
  Video,
} from 'lucide-react'
import type { ReactNode } from 'react'

import { formatDateTime, formatDuration } from '../lib/format'
import type { JobStepView } from '../types/api'
import { ModelUsagePanel } from './ModelUsagePanel'

const STATUS_LABELS = {
  pending: '等待中',
  running: '处理中',
  succeeded: '已完成',
  failed: '执行失败',
  stale: '配置已变更',
  cancelled: '已取消',
  interrupted: '已中断',
} as const

function StatusIcon({ status }: { status: JobStepView['status'] }) {
  if (status === 'running') return <LoaderCircle className="is-spinning" size={17} />
  if (status === 'succeeded') return <Check size={17} />
  if (status === 'failed') return <AlertCircle size={17} />
  if (status === 'stale') return <RefreshCw size={17} />
  return <Clock3 size={17} />
}
interface PipelineStepCardProps {
  step: JobStepView
  index: number
  label: string
  summary: string
  artifactSummary?: string | null
  editing: boolean
  disabled: boolean
  children?: ReactNode
  onToggleEdit: () => void
  onRun: () => void
  onReplaceMedia?: () => void
}

export function PipelineStepCard({
  step,
  index,
  label,
  summary,
  artifactSummary,
  editing,
  disabled,
  children,
  onToggleEdit,
  onRun,
  onReplaceMedia,
}: PipelineStepCardProps) {
  const canConfigure = step.status !== 'running' && !disabled
  const latestAttempt = step.attempts.at(-1)
  const runLabel = step.status === 'failed'
    ? '从此步骤重试'
    : step.status === 'succeeded'
      ? '从此步骤重跑'
      : '从此步骤继续'

  return (
    <article className={`pipeline-step-card is-${step.status} ${editing ? 'is-editing' : ''}`}>
      <header className="pipeline-step-header">
        <span className="pipeline-step-index">{String(index).padStart(2, '0')}</span>
        <div>
          <h3>{label}</h3>
          <span className={`pipeline-step-status is-${step.status}`}>
            <StatusIcon status={step.status} />
            {STATUS_LABELS[step.status]}
          </span>
        </div>
      </header>

      <div className="pipeline-step-body">
        <p className="pipeline-config-summary">{summary}</p>
        <div className="pipeline-attempt-meta">
          <span>配置 v{step.config_revision}</span>
          <span>执行 {step.attempts.length} 次</span>
          <span>
            最近 {latestAttempt?.status === 'running'
              ? '计时中'
              : formatDuration(step.latest_duration_ms)}
          </span>
          <span>累计 {formatDuration(step.total_duration_ms)}</span>
        </div>
        {latestAttempt ? (
          <section className="pipeline-latest-attempt" aria-label="最近一次执行指标">
            <header>
              <strong>最近 Attempt #{latestAttempt.number}</strong>
              <span>{STATUS_LABELS[latestAttempt.status]}</span>
            </header>
            <dl>
              <div><dt>开始</dt><dd>{formatDateTime(latestAttempt.started_at)}</dd></div>
              <div><dt>结束</dt><dd>{formatDateTime(latestAttempt.finished_at)}</dd></div>
              <div>
                <dt>本次耗时</dt>
                <dd>
                  {latestAttempt.status === 'running'
                    ? '计时中'
                    : formatDuration(latestAttempt.duration_ms)}
                </dd>
              </div>
            </dl>
          </section>
        ) : null}
        {latestAttempt?.model_usage ? (
          <ModelUsagePanel
            usage={latestAttempt.model_usage}
            title="最近 Attempt 模型用量"
          />
        ) : null}
        {step.total_model_usage && step.attempts.length > 1 ? (
          <ModelUsagePanel usage={step.total_model_usage} title="历史累计模型用量" />
        ) : null}
        {step.attempts.length > 0 ? (
          <details className="pipeline-attempt-history">
            <summary>
              <History size={13} aria-hidden="true" />
              查看 Attempt 历史（{step.attempts.length}）
            </summary>
            <div className="pipeline-attempt-history-list">
              {[...step.attempts].reverse().map((attempt) => (
                <article key={attempt.number}>
                  <header>
                    <strong>Attempt #{attempt.number}</strong>
                    <span className={`is-${attempt.status}`}>
                      {STATUS_LABELS[attempt.status]}
                    </span>
                  </header>
                  <p>
                    {formatDateTime(attempt.started_at)} → {formatDateTime(attempt.finished_at)}
                  </p>
                  <p>
                    耗时：{attempt.status === 'running'
                      ? '计时中'
                      : formatDuration(attempt.duration_ms)}
                  </p>
                  {attempt.model_usage ? (
                    <ModelUsagePanel usage={attempt.model_usage} title="本次模型用量" compact />
                  ) : null}
                  {attempt.error ? <p className="is-error">{attempt.error}</p> : null}
                </article>
              ))}
            </div>
          </details>
        ) : null}
        {artifactSummary ? (
          <div className="pipeline-artifact-summary">
            {step.id === 'media' ? <Video size={15} /> : <FileOutput size={15} />}
            <span>{artifactSummary}</span>
          </div>
        ) : null}
        {step.error ? (
          <div className="pipeline-step-error" role="alert">
            <AlertCircle size={15} />
            <span>{step.error}</span>
          </div>
        ) : null}
      </div>

      <footer className="pipeline-step-actions">
        {step.id === 'media' && onReplaceMedia ? (
          <button type="button" onClick={onReplaceMedia} disabled={!canConfigure}>
            <Pencil size={15} />
            更换视频
          </button>
        ) : (
          <button type="button" onClick={onToggleEdit} disabled={!canConfigure}>
            <Pencil size={15} />
            {editing ? '收起配置' : '修改配置'}
          </button>
        )}
        <button
          type="button"
          className="pipeline-run-button"
          onClick={onRun}
          disabled={disabled || !step.can_run}
        >
          <Play size={15} fill="currentColor" />
          {runLabel}
        </button>
      </footer>

      {editing ? <div className="pipeline-step-editor">{children}</div> : null}
    </article>
  )
}
