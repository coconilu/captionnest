import {
  AlertCircle,
  CheckCircle2,
  FileVideo2,
  FolderOpen,
  LoaderCircle,
  Play,
  Upload,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import {
  createBatch,
  pickVideos,
  preflightBatch,
  uploadFiles,
} from '../api/client'
import { fileNameFromPath, formatBytes } from '../lib/format'
import type {
  BatchConfigSnapshot,
  BatchCreateResult,
  BatchSourcePreflightView,
  BatchSourceRequest,
} from '../types/api'

interface StagedSource {
  id: string
  name: string
  size?: number
  request?: BatchSourceRequest
  error?: string
}

interface BatchCreatorProps {
  connected: boolean
  config: BatchConfigSnapshot
  configError: string | null
  startError: string | null
  runtimeApiKey: string
  children?: ReactNode
  onBusyChange: (busy: boolean) => void
  onCreated: (result: BatchCreateResult, runtimeApiKey: string) => void
  onClose: () => void
}

function stagedId(): string {
  return typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function BatchCreator({
  connected,
  config,
  configError,
  startError,
  runtimeApiKey,
  children,
  onBusyChange,
  onCreated,
  onClose,
}: BatchCreatorProps) {
  const [sources, setSources] = useState<StagedSource[]>([])
  const [batchName, setBatchName] = useState('')
  const [picking, setPicking] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [preflightBusy, setPreflightBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [preflight, setPreflight] = useState<BatchSourcePreflightView[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [resultMessage, setResultMessage] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const resolvedSources = useMemo(
    () => sources.filter((source): source is StagedSource & { request: BatchSourceRequest } =>
      Boolean(source.request)),
    [sources],
  )
  const preflightById = useMemo(() => new Map(
    resolvedSources.map((source, index) => [source.id, preflight[index]]),
  ), [preflight, resolvedSources])
  const validCount = preflight.filter((item) => item.valid).length
  const invalidCount = sources.length - validCount

  useEffect(() => {
    onBusyChange(picking || uploading || creating)
  }, [creating, onBusyChange, picking, uploading])

  useEffect(() => () => onBusyChange(false), [onBusyChange])

  useEffect(() => {
    if (!connected || !resolvedSources.length || configError) {
      setPreflight([])
      setPreflightBusy(false)
      return
    }

    const controller = new AbortController()
    setPreflight([])
    setPreflightBusy(true)
    const timeoutId = window.setTimeout(async () => {
      try {
        const result = await preflightBatch({
          sources: resolvedSources.map((source) => source.request),
          config,
        }, controller.signal)
        setPreflight(result.items)
        setActionError(null)
      } catch (error) {
        if (controller.signal.aborted) return
        setPreflight([])
        setActionError(error instanceof Error ? error.message : '无法预检批次文件')
      } finally {
        if (!controller.signal.aborted) setPreflightBusy(false)
      }
    }, 280)

    return () => {
      controller.abort()
      window.clearTimeout(timeoutId)
    }
  }, [config, configError, connected, resolvedSources])

  const addPathSources = async () => {
    setPicking(true)
    setActionError(null)
    setResultMessage(null)
    try {
      const picked = await pickVideos()
      const additions = picked.flatMap((item) => item.path
        ? [{
            id: stagedId(),
            name: item.name ?? fileNameFromPath(item.path),
            size: item.size ?? undefined,
            request: { video_path: item.path },
          }]
        : [])
      if (additions.length) setSources((current) => [...current, ...additions])
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法选择本机视频')
    } finally {
      setPicking(false)
    }
  }

  const addBrowserFiles = async (files: File[]) => {
    if (!files.length) return
    setUploading(true)
    setActionError(null)
    setResultMessage(null)
    try {
      const result = await uploadFiles(files)
      const additions: StagedSource[] = result.results.map((item) => {
        const file = files[item.index]
        if (item.ok && item.upload) {
          return {
            id: stagedId(),
            name: item.upload.name,
            size: item.upload.size,
            request: { upload_id: item.upload.upload_id },
          }
        }
        return {
          id: stagedId(),
          name: item.name || file?.name || `文件 ${item.index + 1}`,
          size: file?.size,
          error: item.error ?? '上传失败',
        }
      })
      setSources((current) => [...current, ...additions])
      if (result.failed) setActionError(`${result.failed} 个文件上传失败，可移除后继续`)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '多文件上传失败')
    } finally {
      setUploading(false)
    }
  }

  const create = async (autoStart: boolean) => {
    const validationError = autoStart ? startError ?? configError : configError
    if (validationError) {
      setActionError(validationError)
      return
    }
    if (!resolvedSources.length || preflightBusy || validCount === 0) {
      setActionError('请先添加并通过预检的有效视频')
      return
    }

    setCreating(true)
    setActionError(null)
    setResultMessage(null)
    try {
      const key = autoStart ? runtimeApiKey.trim() : ''
      const result = await createBatch({
        name: batchName.trim() || undefined,
        sources: resolvedSources.map((source) => source.request),
        config,
        auto_start: autoStart,
        api_key: key || undefined,
      })
      const successfulSourceIds = new Set(
        result.results
          .filter((item) => item.ok)
          .map((item) => resolvedSources[item.index]?.id)
          .filter((id): id is string => Boolean(id)),
      )
      const failedBySourceId = new Map(
        result.results
          .filter((item) => !item.ok)
          .map((item) => [resolvedSources[item.index]?.id, item.error ?? '创建失败']),
      )
      setSources((current) => current
        .filter((source) => !successfulSourceIds.has(source.id))
        .map((source) => ({ ...source, error: failedBySourceId.get(source.id) ?? source.error })))
      setResultMessage(
        `已创建 ${result.created_count} 个任务${result.failed_count ? `，${result.failed_count} 个失败` : ''}`,
      )
      if (result.created_count) setBatchName('')
      onCreated(result, key)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法创建批次')
    } finally {
      setCreating(false)
    }
  }

  const busy = picking || uploading || creating
  const createDisabled = busy
    || preflightBusy
    || Boolean(configError)
    || validCount === 0

  return (
    <section className="batch-creator" aria-labelledby="batch-files-title">
      <div className="batch-creator-main">
        <header>
          <div>
            <span className="panel-step-label">文件与批次</span>
            <h3 id="batch-files-title">添加视频</h3>
            <p>每个文件会创建独立任务，公共配置只在创建时复制。</p>
          </div>
        </header>

        <label className="field batch-name-field">
          <span>批次名称（可选）</span>
          <input
            value={batchName}
            onChange={(event) => setBatchName(event.target.value)}
            maxLength={160}
            placeholder="例如：课程第 3 章"
            disabled={busy}
          />
        </label>

        <div
          className={`batch-drop-zone ${dragging ? 'is-dragging' : ''}`}
          onDragEnter={(event) => {
            event.preventDefault()
            if (!busy) setDragging(true)
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            if (!busy) void addBrowserFiles([...event.dataTransfer.files])
          }}
        >
          {uploading || picking ? <LoaderCircle className="is-spinning" /> : <Upload />}
          <strong>{dragging ? '松开即可上传' : '拖入多个视频，或选择来源'}</strong>
          <span>支持 MP4、MKV、MOV、AVI、WEBM、M4V、TS、MTS、M2TS</span>
          <div>
            <button type="button" className="button button-secondary" onClick={() => void addPathSources()} disabled={busy || !connected}>
              <FolderOpen size={16} />
              本机路径
            </button>
            <button type="button" className="button button-ghost" onClick={() => fileInputRef.current?.click()} disabled={busy || !connected}>
              <Upload size={16} />
              浏览器上传
            </button>
            <input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              multiple
              accept="video/*,.mkv,.m4v,.ts,.mts,.m2ts"
              onChange={(event) => {
                void addBrowserFiles([...(event.target.files ?? [])])
                event.target.value = ''
              }}
            />
          </div>
        </div>

        <div className="batch-source-summary" aria-live="polite">
          <span>{sources.length} 个文件</span>
          <span className="is-valid">{validCount} 个有效</span>
          {invalidCount ? <span className="is-invalid">{invalidCount} 个待处理</span> : null}
          {preflightBusy ? <span><LoaderCircle size={12} className="is-spinning" />预检中</span> : null}
        </div>

        <div className="batch-source-list">
          {sources.map((source) => {
            const item = preflightById.get(source.id)
            const issues = source.error
              ? [source.error]
              : item?.issues.map((issue) => issue.message) ?? []
            const valid = Boolean(item?.valid) && !source.error
            return (
              <article key={source.id} className={`batch-source-row ${issues.length ? 'is-invalid' : valid ? 'is-valid' : ''}`}>
                <span className="batch-source-icon"><FileVideo2 size={18} /></span>
                <span className="batch-source-copy">
                  <strong title={source.name}>{source.name}</strong>
                  <small>
                    {formatBytes(source.size)}
                    {item?.output_path ? ` · 输出 ${item.output_path}` : ''}
                  </small>
                  {issues.map((issue, index) => <em key={`${index}-${issue}`}>{issue}</em>)}
                </span>
                <span className="batch-source-state" aria-label={valid ? '预检通过' : issues.length ? '预检失败' : '等待预检'}>
                  {valid ? <CheckCircle2 size={17} /> : issues.length ? <AlertCircle size={17} /> : <LoaderCircle size={17} className="is-spinning" />}
                </span>
                <button
                  type="button"
                  className="icon-button"
                  disabled={busy}
                  onClick={() => setSources((current) => current.filter((itemSource) => itemSource.id !== source.id))}
                  aria-label={`移除 ${source.name}`}
                >
                  <X size={16} />
                </button>
              </article>
            )
          })}
          {!sources.length ? <p className="batch-source-empty">尚未添加文件</p> : null}
        </div>

        {configError || actionError ? (
          <div className="inline-error" role="alert">
            <AlertCircle size={17} />
            <span>{actionError ?? configError}</span>
          </div>
        ) : null}
        {resultMessage ? (
          <div className="batch-create-success" role="status">
            <CheckCircle2 size={17} />
            <span>{resultMessage}</span>
          </div>
        ) : null}
      </div>

      {children ? <div className="batch-creator-settings">{children}</div> : null}

      <footer>
        <p>
          <strong>准备创建 {validCount} 个任务</strong>
          <span>无效项不会阻止其余任务创建，错误会逐文件保留。</span>
        </p>
        <div>
          <button type="button" className="button button-ghost" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button type="button" className="button button-ghost" disabled={createDisabled} onClick={() => void create(false)}>
            仅创建
          </button>
          <button
            type="button"
            className="button button-secondary"
            disabled={createDisabled || Boolean(startError)}
            title={startError ?? undefined}
            onClick={() => void create(true)}
          >
            {creating ? <LoaderCircle size={16} className="is-spinning" /> : <Play size={16} fill="currentColor" />}
            创建并启动
          </button>
        </div>
      </footer>
    </section>
  )
}
