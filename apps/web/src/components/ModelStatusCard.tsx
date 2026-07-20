import { Download, HardDrive, LoaderCircle } from 'lucide-react'

import type { ModelItem, ModelStatus } from '../types/api'

interface ModelStatusCardProps {
  modelId: string
  item: ModelItem | null
  fallbackStatus?: ModelStatus
  fallbackMessage?: string | null
  modelRoot: string
  checking: boolean
  downloading: boolean
  disabled: boolean
  error: string | null
  onDownload: (id: string) => void
}

const STATUS_LABEL: Record<ModelStatus, string> = {
  ready: '已就绪',
  missing: '尚未下载',
  downloading: '正在下载',
  damaged: '需要重新下载',
}

export function ModelStatusCard({
  modelId,
  item,
  fallbackStatus,
  fallbackMessage,
  modelRoot,
  checking,
  downloading,
  disabled,
  error,
  onDownload,
}: ModelStatusCardProps) {
  const status = item?.status ?? fallbackStatus
  const activeDownload = downloading || status === 'downloading'
  const progress = item?.progress == null ? null : Math.max(0, Math.min(100, item.progress))
  const canDownload = status === 'missing' || status === 'damaged'
  const rawMessage = item?.message ?? fallbackMessage
  const statusLabel = status ? STATUS_LABEL[status] : null
  const statusMessage = rawMessage && rawMessage !== statusLabel
    ? rawMessage
    : status === 'ready'
      ? '模型已下载并可用'
      : '识别模型按需保存在本机，不随安装包重复分发。'

  return (
    <div className={`model-status-card ${status === 'ready' ? 'is-ready' : canDownload ? 'is-warning' : ''}`}>
      <div className="model-status-heading">
        <span className="environment-icon" aria-hidden="true">
          <HardDrive size={17} />
        </span>
        <div>
          <strong>{item?.label ?? modelId}</strong>
          <span>{status ? STATUS_LABEL[status] : checking ? '正在检查' : '状态未知'}</span>
        </div>
      </div>
      <p>{statusMessage}</p>
      {activeDownload ? (
        <div
          className={`model-progress ${progress == null ? 'is-indeterminate' : ''}`}
          role="progressbar"
          aria-label={progress == null ? '模型正在下载' : `模型下载进度 ${Math.round(progress)}%`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress ?? undefined}
        >
          <span style={progress == null ? undefined : { width: `${progress}%` }} />
          <small>{progress == null ? '正在准备下载，进度将自动更新' : `${Math.round(progress)}% · 自动更新`}</small>
        </div>
      ) : null}
      {item?.path || modelRoot ? (
        <small className="model-path" title={item?.path ?? modelRoot}>{item?.path ?? modelRoot}</small>
      ) : null}
      {error ? <span className="environment-error" role="alert">{error}</span> : null}
      <div className="environment-actions">
        {canDownload ? (
          <button
            type="button"
            className="primary-environment-action"
            onClick={() => onDownload(modelId)}
            disabled={disabled || downloading}
          >
            {downloading ? <LoaderCircle className="is-spinning" size={14} /> : <Download size={14} />}
            {downloading ? '正在启动' : status === 'damaged' ? '重新下载模型' : '下载模型'}
          </button>
        ) : null}
      </div>
    </div>
  )
}
