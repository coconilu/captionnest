import { ExternalLink, LoaderCircle, RefreshCw, Terminal } from 'lucide-react'
import type { MouseEvent } from 'react'

import type { EnvironmentView } from '../types/api'

interface CodexStatusCardProps {
  codex: EnvironmentView['codex'] | null
  checking: boolean
  onRefresh: () => void
}

const DEFAULT_INSTALL_URL = 'https://developers.openai.com/codex/cli/'

async function openWithTauri(url: string) {
  try {
    const { openUrl } = await import('@tauri-apps/plugin-opener')
    await openUrl(url)
  } catch {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
}

export function CodexStatusCard({ codex, checking, onRefresh }: CodexStatusCardProps) {
  const status = codex?.status
  const ready = status === 'ready'
  const missing = status === 'not_installed'
  const notLoggedIn = status === 'not_logged_in'
  const title = ready
    ? 'Codex 已就绪'
    : missing
      ? '未检测到 Codex'
      : notLoggedIn
        ? 'Codex 尚未登录'
        : status === 'check_failed'
          ? 'Codex 检测失败'
          : '正在检测 Codex'
  const message = codex?.message
    ?? (missing
      ? '安装 Codex CLI 后，返回此处刷新检测。'
      : notLoggedIn
        ? '请在终端运行 codex login，通过浏览器完成 ChatGPT 登录。'
        : ready
          ? '将通过本机 Codex CLI 与现有 ChatGPT 登录翻译。'
          : '正在读取安装与登录状态。')
  const installUrl = codex?.install_url ?? DEFAULT_INSTALL_URL

  const handleInstallLink = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!('__TAURI_INTERNALS__' in window)) return
    event.preventDefault()
    void openWithTauri(installUrl)
  }

  return (
    <div className={`codex-status-card ${ready ? 'is-ready' : missing || notLoggedIn ? 'is-warning' : ''}`}>
      <div className="codex-status-heading">
        <span className="environment-icon" aria-hidden="true">
          <Terminal size={17} />
        </span>
        <div>
          <strong>{title}</strong>
          {ready && codex?.version ? <span>版本 {codex.version}</span> : null}
        </div>
      </div>
      <p>{message}</p>
      {notLoggedIn ? <code>codex login</code> : null}
      <div className="environment-actions">
        {missing ? (
          <a
            href={installUrl}
            target="_blank"
            rel="noreferrer"
            onClick={handleInstallLink}
          >
            查看安装说明
            <ExternalLink size={14} aria-hidden="true" />
          </a>
        ) : null}
        <button type="button" onClick={onRefresh} disabled={checking}>
          {checking ? <LoaderCircle className="is-spinning" size={14} /> : <RefreshCw size={14} />}
          {checking ? '检测中' : '刷新检测'}
        </button>
      </div>
    </div>
  )
}
