import { FileVideo2, FolderOpen, LoaderCircle, Video, X } from 'lucide-react'

import { formatBytes } from '../lib/format'

export interface SelectedSource {
  kind: 'path'
  name: string
  path: string
  size?: number
}

interface SourcePickerProps {
  source: SelectedSource | null
  busy: boolean
  disabled: boolean
  onPickPath: () => Promise<void>
  onClear: () => void
}

export function SourcePicker({
  source,
  busy,
  disabled,
  onPickPath,
  onClear,
}: SourcePickerProps) {
  return (
    <section className="source-region" aria-labelledby="source-title">
      <div
        className={`drop-zone ${disabled ? 'is-disabled' : ''}`}
      >
        <span className="panel-step-label">01 · 选择视频</span>
        <div className="drop-icon" aria-hidden="true">
          {busy ? <LoaderCircle className="is-spinning" /> : <Video />}
        </div>
        <h2 id="source-title">{busy ? '正在选择视频…' : '选择视频开始处理'}</h2>
        <p>支持 MP4、MKV、MOV、WEBM</p>
        <div className="source-actions">
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void onPickPath()}
            disabled={disabled || busy}
          >
            <FolderOpen size={17} aria-hidden="true" />
            选择本机文件
          </button>
        </div>
        <div className="boundary-note">
          <span aria-hidden="true">✓</span>
          视频与音频留在本机，在线翻译仅发送字幕文本
        </div>
      </div>

      {source ? (
        <div className="file-row">
          <div className="file-icon" aria-hidden="true">
            <FileVideo2 />
          </div>
          <div className="file-details">
            <strong>{source.name}</strong>
            <span>
              {formatBytes(source.size)}
              <i>·</i>
              使用原始文件路径
            </span>
            <small title={source.path}>{source.path}</small>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClear}
            disabled={disabled}
            aria-label={`移除 ${source.name}`}
          >
            <X size={19} />
          </button>
        </div>
      ) : null}
    </section>
  )
}
