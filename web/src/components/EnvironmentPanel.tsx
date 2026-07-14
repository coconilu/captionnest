import { Box, Cpu, Film, LoaderCircle } from 'lucide-react'

import type { AsrModel, EnvironmentView, ModelItem, ModelStatus } from '../types/api'
import { CodexStatusCard } from './CodexStatusCard'
import { ModelStatusCard } from './ModelStatusCard'

interface EnvironmentPanelProps {
  environment: EnvironmentView | null
  checking: boolean
  error: string | null
  selectedModel: AsrModel
  models: ModelItem[]
  modelRoot: string
  modelsChecking: boolean
  modelsError: string | null
  downloadingModelId: string | null
  disabled: boolean
  onRefresh: () => void
  onDownloadModel: (id: string) => void
}

interface EnvironmentItemProps {
  icon: 'runtime' | 'asr' | 'acceleration' | 'media'
  label: string
  value: string
  ready?: boolean
}

function EnvironmentItem({ icon, label, value, ready }: EnvironmentItemProps) {
  const Icon = icon === 'acceleration' ? Cpu : icon === 'media' ? Film : icon === 'asr' ? Box : Cpu
  return (
    <div className={`environment-item ${ready ? 'is-ready' : ''}`}>
      <Icon size={15} aria-hidden="true" />
      <span>
        <small>{label}</small>
        <strong title={value}>{value}</strong>
      </span>
    </div>
  )
}

export function EnvironmentPanel({
  environment,
  checking,
  error,
  selectedModel,
  models,
  modelRoot,
  modelsChecking,
  modelsError,
  downloadingModelId,
  disabled,
  onRefresh,
  onDownloadModel,
}: EnvironmentPanelProps) {
  const selected = models.find((item) => item.id === selectedModel) ?? null
  const environmentModelMatches = environment?.model.name === selectedModel
  const fallbackStatus: ModelStatus | undefined = environmentModelMatches ? environment.model.status : undefined
  const runtimeReady = environment?.runtime.status === 'ready'
  const asrReady = environment?.asr.status === 'ready'
  const mediaReady = environment?.tools.media.status === 'ready'
  const accelerationReady = environment?.acceleration.status === 'cuda_ready' || environment?.acceleration.status === 'cpu'

  return (
    <section className="settings-section environment-section" aria-labelledby="environment-title">
      <div className="section-heading environment-section-heading">
        <div>
          <h2 id="environment-title">环境状态</h2>
          <span>{checking ? '正在检测本机能力…' : '必需组件随应用运行，可选能力按需启用'}</span>
        </div>
        {checking ? <LoaderCircle className="is-spinning" size={17} aria-hidden="true" /> : null}
      </div>

      {error ? <span className="environment-error" role="alert">{error}</span> : null}

      <div className="environment-grid" aria-live="polite">
        <EnvironmentItem
          icon="runtime"
          label="应用运行时"
          value={runtimeReady
            ? `Python ${environment?.runtime.version ?? ''}`.trim()
            : environment?.runtime.message ?? (checking ? '检测中' : '不可用')}
          ready={runtimeReady}
        />
        <EnvironmentItem
          icon="asr"
          label="语音识别"
          value={asrReady
            ? `${environment?.asr.provider ?? 'Faster-Whisper'} ${environment?.asr.version ?? ''}`.trim()
            : environment?.asr.message ?? (checking ? '检测中' : '不可用')}
          ready={asrReady}
        />
        <EnvironmentItem
          icon="acceleration"
          label="计算设备"
          value={environment?.acceleration.status === 'cuda_ready'
            ? 'CUDA 加速'
            : environment?.acceleration.status === 'cuda_unavailable'
              ? 'CUDA 不可用，使用 CPU'
              : checking ? '检测中' : 'CPU 模式'}
          ready={accelerationReady}
        />
        <EnvironmentItem
          icon="media"
          label="媒体解码"
          value={mediaReady
            ? environment?.tools.media.provider ?? 'PyAV'
            : environment?.tools.media.message ?? (checking ? '检测中' : '不可用')}
          ready={mediaReady}
        />
      </div>

      <ModelStatusCard
        modelId={selectedModel}
        item={selected}
        fallbackStatus={fallbackStatus}
        fallbackMessage={environmentModelMatches ? environment.model.message : null}
        modelRoot={modelRoot}
        checking={modelsChecking}
        downloading={downloadingModelId === selectedModel}
        disabled={disabled}
        error={modelsError}
        onDownload={onDownloadModel}
        onRefresh={onRefresh}
      />

      <CodexStatusCard codex={environment?.codex ?? null} checking={checking || modelsChecking} onRefresh={onRefresh} />
    </section>
  )
}
