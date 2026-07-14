import { AlertCircle, CheckCircle2, CircleDot, FolderOpen, Info, LoaderCircle } from 'lucide-react'

import { openFolder } from '../api/client'
import { fileNameFromPath, formatLog, formatTime } from '../lib/format'
import type { JobView } from '../types/api'

interface TaskConsoleProps {
  job: JobView | null
  pollError: string | null
  actionError: string | null
  onActionError: (message: string | null) => void
}

function LogIcon({ level }: { level?: string }) {
  if (level === 'success') return <CheckCircle2 className="log-success" size={16} />
  if (level === 'error') return <AlertCircle className="log-error" size={16} />
  if (level === 'warning') return <CircleDot className="log-warning" size={16} />
  return <Info className="log-info" size={16} />
}

export function TaskConsole({ job, pollError, actionError, onActionError }: TaskConsoleProps) {
  const logs = job?.logs?.map(formatLog) ?? []
  const progress = Math.max(0, Math.min(100, job?.progress ?? 0))
  const subtitlePath = job?.subtitle_path
  const done = job?.status === 'completed'
  const error = job?.error ?? pollError ?? actionError

  const handleOpen = async (path?: string | null) => {
    if (!path) return
    try {
      await openFolder(path)
      onActionError(null)
    } catch (openError) {
      onActionError(openError instanceof Error ? openError.message : '无法打开输出目录')
    }
  }

  return (
    <section className="console-panel" aria-labelledby="console-title">
      <div className="console-header">
        <div>
          <h2 id="console-title">处理日志</h2>
          <span>{job ? `任务 ${job.id.slice(0, 8)}` : '开始任务后可在这里查看实时进度'}</span>
        </div>
        {job && !done && job.status !== 'failed' && job.status !== 'cancelled' ? (
          <span className="running-label"><LoaderCircle size={15} className="is-spinning" />正在处理</span>
        ) : null}
      </div>

      {error ? (
        <div className="inline-error" role="alert">
          <AlertCircle size={17} />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="console-grid">
        <div className="log-list" aria-live="polite">
          {logs.length ? (
            logs.map((log, index) => (
              <div className="log-row" key={`${log.timestamp ?? 'log'}-${index}`}>
                <time>{formatTime(log.timestamp)}</time>
                <LogIcon level={log.level} />
                <span>{log.message}</span>
              </div>
            ))
          ) : (
            <div className="empty-logs">
              <Info size={18} />
              <span>{job ? '任务已创建，等待第一条处理消息…' : '尚无任务日志'}</span>
            </div>
          )}
        </div>

        {done ? (
          <aside className="result-card" aria-label="字幕生成结果">
            <div className="result-heading">
              <CheckCircle2 size={22} />
              <div>
                <strong>字幕已生成</strong>
                <span>双语字幕已保存</span>
              </div>
            </div>
            <div className="result-files">
              {subtitlePath ? (
                <div className="result-file">
                  <div>
                    <strong>{fileNameFromPath(subtitlePath)}</strong>
                    <span>双语字幕 · 源文在上，译文在下</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleOpen(subtitlePath)}
                    aria-label={`打开 ${subtitlePath} 所在文件夹`}
                  >
                    <FolderOpen size={16} />
                    打开
                  </button>
                </div>
              ) : (
                <span className="result-path-missing">字幕路径暂不可用</span>
              )}
            </div>
            <p title={subtitlePath ?? ''}>{subtitlePath ?? '已保存至视频同目录'}</p>
            <button
              type="button"
              className="open-folder-button"
              onClick={() => void handleOpen(subtitlePath)}
              disabled={!subtitlePath}
            >
              <FolderOpen size={17} />
              打开文件夹
            </button>
          </aside>
        ) : null}
      </div>

      <div className="progress-row">
        <div
          className="progress-track"
          role="progressbar"
          aria-label={`总进度 ${Math.round(progress)}%`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
        <strong>{Math.round(progress)}%</strong>
      </div>
    </section>
  )
}
