import { Boxes, FolderOpen, GitBranch } from 'lucide-react'
import { useState } from 'react'

import { openOutputFolder } from '../lib/actions'
import { formatBytes, formatDuration, formatTokenCount } from '../lib/format'
import type {
  AsrStepConfig,
  ExportStepConfig,
  JobStepConfig,
  JobStepId,
  JobStepView,
  JobView,
  MediaStepConfig,
  TranslationStepConfig,
} from '../types/api'
import { AsrStepEditor } from './AsrStepEditor'
import { ExportStepEditor } from './ExportStepEditor'
import { PipelineStepCard } from './PipelineStepCard'
import { TranslationStepEditor } from './TranslationStepEditor'

const STEP_LABELS: Record<JobStepId, string> = {
  media: '媒体准备',
  transcription: '语音识别',
  translation: '字幕翻译',
  export: '字幕导出',
}

const PREVIEW_STEPS: JobStepId[] = ['media', 'transcription', 'translation', 'export']

interface StepSummary {
  text: string
  title?: string
}

function configSummary(step: JobStepView): StepSummary {
  if (step.id === 'media') {
    const config = step.config as MediaStepConfig
    return { text: config.name }
  }
  if (step.id === 'transcription') {
    const config = step.config as AsrStepConfig
    const device = config.device === 'cuda' ? 'GPU' : 'CPU'
    const mode = config.output_mode === 'word_resegmented' ? '逐词重排' : '原始分片'
    const boundary = config.dynamic_chunking ? '动态边界' : '固定边界'
    const retry = config.selective_retry ? '二次识别' : '单次识别'
    const timestamps = config.timestamp_normalization ? '实验校时' : '原始时间轴'
    const hotwords = config.hotwords?.length ? ` · ${config.hotwords.length} 个提示词` : ''
    return { text: `${config.model} · ${device} · ${mode} · ${boundary} · ${retry} · ${timestamps}${hotwords}` }
  }
  if (step.id === 'translation') {
    const config = step.config as TranslationStepConfig
    const target = { 'zh-CN': '简体中文', en: '英语', ko: '韩语' }[config.target_language]
    const provider = {
      codex_spark: 'Codex Spark',
      lmstudio: 'LM Studio',
      deepseek: 'DeepSeek',
    }[config.provider]
    return {
      text: `${target} · ${provider}`,
      title: config.model ? `${target} · ${provider} · ${config.model}` : undefined,
    }
  }
  const config = step.config as ExportStepConfig
  return {
    text: config.output_directory
      ? `输出至 ${config.output_directory}`
      : '输出至源视频目录 · 双语 SRT',
  }
}

function artifactSummary(step: JobStepView): string | null {
  const summary = step.artifact?.summary
  if (!summary) return null
  if (step.id === 'media') {
    return `${String(summary.name ?? '媒体文件')} · ${formatBytes(Number(summary.size ?? 0))}`
  }
  if (step.id === 'transcription') {
    const hotwords = Number(summary.hotword_count ?? 0)
    return `${String(summary.segment_count ?? 0)} 条字幕 · 语言 ${String(summary.language ?? '未知')}${hotwords ? ` · ${hotwords} 个提示词` : ''}`
  }
  if (step.id === 'translation') {
    return `${String(summary.item_count ?? 0)} 条译文 · ${String(summary.target_language ?? '')}`
  }
  return `${String(summary.name ?? '字幕文件')} · ${formatBytes(Number(summary.size ?? 0))}`
}

interface WorkflowPipelineProps {
  job: JobView | null
  disabled: boolean
  cudaAvailable: boolean
  apiKey: string
  onApiKeyChange: (value: string) => void
  onUpdateStep: (step: JobStepId, config: JobStepConfig) => Promise<void>
  onRunStep: (step: JobStepId) => Promise<void>
  onReplaceMedia: () => Promise<void>
  onActionError: (message: string | null) => void
}

export function WorkflowPipeline({
  job,
  disabled,
  cudaAvailable,
  apiKey,
  onApiKeyChange,
  onUpdateStep,
  onRunStep,
  onReplaceMedia,
  onActionError,
}: WorkflowPipelineProps) {
  const [editingStep, setEditingStep] = useState<JobStepId | null>(null)
  const [savingStep, setSavingStep] = useState<JobStepId | null>(null)
  const [runningStep, setRunningStep] = useState<JobStepId | null>(null)

  const save = async (step: JobStepId, config: JobStepConfig) => {
    setSavingStep(step)
    try {
      await onUpdateStep(step, config)
      setEditingStep(null)
    } catch {
      // App owns the user-facing error banner; keep the editor open for correction.
    } finally {
      setSavingStep(null)
    }
  }

  const run = async (step: JobStepId) => {
    setRunningStep(step)
    try {
      await onRunStep(step)
      setEditingStep(null)
    } catch {
      // App owns the user-facing error banner; keep the current step visible.
    } finally {
      setRunningStep(null)
    }
  }

  if (!job) {
    return (
      <section className="pipeline-workbench is-empty" aria-labelledby="pipeline-title">
        <div className="pipeline-heading">
          <div>
            <span className="panel-step-label">任务流水线</span>
            <h2 id="pipeline-title">每一步都有独立配置和产物</h2>
          </div>
          <GitBranch size={21} aria-hidden="true" />
        </div>
        <div className="pipeline-preview" aria-label="待创建任务的四个步骤">
          {PREVIEW_STEPS.map((step, index) => (
            <div key={step}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <strong>{STEP_LABELS[step]}</strong>
              <small>等待任务</small>
            </div>
          ))}
        </div>
        <p className="pipeline-empty-note">
          <Boxes size={17} />
          选择视频并开始后，识别、翻译和导出产物会在这里逐步出现。
        </p>
      </section>
    )
  }

  return (
    <section className="pipeline-workbench" aria-labelledby="pipeline-title">
      <div className="pipeline-heading">
        <div>
          <span className="panel-step-label">任务流水线 · {job.id.slice(0, 8)}</span>
          <h2 id="pipeline-title">处理步骤</h2>
        </div>
        <span className={`pipeline-job-status is-${job.status}`}>
          {job.status === 'completed'
            ? '全部完成'
            : job.status === 'failed'
              ? '等待修复'
            : job.status === 'waiting_for_input'
              ? '等待运行时输入'
            : job.status === 'interrupted'
              ? '已中断，可重试'
              : job.status === 'cancelled'
                ? '已取消'
              : job.status === 'draft'
                ? '等待运行'
                : '正在处理'}
        </span>
      </div>

      <details className="pipeline-metrics-disclosure">
        <summary>任务累计指标</summary>
        <div className="pipeline-job-metrics" aria-label="任务累计指标">
          <div>
            <span>自然时间跨度</span>
            <strong>{job.wall_duration_ms === null
              ? '尚未开始'
              : formatDuration(job.wall_duration_ms)}</strong>
          </div>
          <div>
            <span>Attempt 累计耗时</span>
            <strong>{job.cumulative_attempt_duration_ms === null
              ? '尚无完整记录'
              : formatDuration(job.cumulative_attempt_duration_ms)}</strong>
          </div>
          <div>
            <span>模型总 Token</span>
            <strong>{job.total_model_usage
              ? formatTokenCount(job.total_model_usage.total_tokens)
              : '尚无模型调用'}</strong>
            {job.total_model_usage ? (
              <small>
                {job.total_model_usage.request_count} 次请求 · {job.total_model_usage.complete
                  ? '完整报告'
                  : '部分报告'}
              </small>
            ) : null}
          </div>
        </div>
      </details>

      <div className="pipeline-step-grid">
        {job.steps.map((step, index) => {
          const editing = editingStep === step.id
          const busy = disabled || savingStep !== null || runningStep !== null
          const summary = configSummary(step)
          const exportReady = step.id === 'export'
            && step.status === 'succeeded'
            && Boolean(step.artifact?.path)
          return (
            <PipelineStepCard
              key={step.id}
              step={step}
              index={index + 1}
              label={STEP_LABELS[step.id]}
              summary={summary.text}
              summaryTitle={summary.title}
              artifactSummary={artifactSummary(step)}
              editing={editing}
              disabled={busy}
              headerAction={exportReady ? (
                <button
                  type="button"
                  className="pipeline-open-folder-button"
                  onClick={() => void openOutputFolder(step.artifact?.path, onActionError)}
                >
                  <FolderOpen size={15} />
                  打开所在文件夹
                </button>
              ) : undefined}
              onToggleEdit={() => setEditingStep(editing ? null : step.id)}
              onRun={() => void run(step.id)}
              onReplaceMedia={step.id === 'media'
                ? () => void onReplaceMedia()
                : undefined}
            >
              {step.id === 'transcription' ? (
                <AsrStepEditor
                  key={`${job.id}-${step.config_revision}`}
                  value={step.config as AsrStepConfig}
                  cudaAvailable={cudaAvailable}
                  saving={savingStep === step.id}
                  onCancel={() => setEditingStep(null)}
                  onSave={(config) => void save(step.id, config)}
                />
              ) : null}
              {step.id === 'translation' ? (
                <TranslationStepEditor
                  key={`${job.id}-${step.config_revision}`}
                  value={step.config as TranslationStepConfig}
                  apiKey={apiKey}
                  saving={savingStep === step.id}
                  onApiKeyChange={onApiKeyChange}
                  onCancel={() => setEditingStep(null)}
                  onSave={(config) => void save(step.id, config)}
                />
              ) : null}
              {step.id === 'export' ? (
                <ExportStepEditor
                  key={`${job.id}-${step.config_revision}`}
                  value={step.config as ExportStepConfig}
                  saving={savingStep === step.id}
                  onCancel={() => setEditingStep(null)}
                  onSave={(config) => void save(step.id, config)}
                />
              ) : null}
            </PipelineStepCard>
          )
        })}
      </div>
    </section>
  )
}
