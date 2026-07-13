import { FileVideo2, FolderOpen, LoaderCircle, Upload, Video, X } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'

import { formatBytes } from '../lib/format'

export interface SelectedSource {
  kind: 'upload' | 'path'
  name: string
  path: string
  size?: number
  uploadId?: string
}

interface SourcePickerProps {
  source: SelectedSource | null
  busy: boolean
  disabled: boolean
  onUpload: (file: File) => Promise<void>
  onPickPath: () => Promise<void>
  onClear: () => void
}

const ACCEPT = '.mp4,.mkv,.mov,.webm,.m4v,.avi'

export function SourcePicker({
  source,
  busy,
  disabled,
  onUpload,
  onPickPath,
  onClear,
}: SourcePickerProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (disabled || busy) return
    const file = event.dataTransfer.files.item(0)
    if (file) void onUpload(file)
  }

  return (
    <section className="source-region" aria-labelledby="source-title">
      <div
        className={`drop-zone ${dragging ? 'is-dragging' : ''} ${disabled ? 'is-disabled' : ''}`}
        onDragEnter={(event) => {
          event.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          if (event.currentTarget === event.target) setDragging(false)
        }}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          className="sr-only"
          type="file"
          accept={ACCEPT}
          disabled={disabled || busy}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void onUpload(file)
            event.currentTarget.value = ''
          }}
        />
        <div className="drop-icon" aria-hidden="true">
          {busy ? <LoaderCircle className="is-spinning" /> : <Video />}
        </div>
        <h2 id="source-title">{busy ? '正在导入视频…' : '拖入视频开始处理'}</h2>
        <p>支持 MP4、MKV、MOV、WEBM</p>
        <div className="source-actions">
          <button
            type="button"
            className="button button-secondary"
            onClick={() => inputRef.current?.click()}
            disabled={disabled || busy}
          >
            <Upload size={17} aria-hidden="true" />
            浏览器上传
          </button>
          <button
            type="button"
            className="button button-ghost"
            onClick={() => void onPickPath()}
            disabled={disabled || busy}
          >
            <FolderOpen size={17} aria-hidden="true" />
            选择本机文件
          </button>
        </div>
        <p className="boundary-note">
          浏览器拖入或上传会复制文件到本地服务；“选择本机文件”只传递路径，不复制视频。
        </p>
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
              {source.kind === 'upload' ? '已上传本地副本' : '使用原始文件路径'}
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
