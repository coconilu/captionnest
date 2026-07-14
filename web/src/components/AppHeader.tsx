import { Cpu, Settings } from 'lucide-react'

interface AppHeaderProps {
  connected: boolean
  checking: boolean
  cudaAvailable?: boolean
  onRefresh: () => void
}

export function AppHeader({ connected, checking, cudaAvailable, onRefresh }: AppHeaderProps) {
  const statusLabel = checking ? '正在连接' : connected ? '本地服务已连接' : '本地服务未连接'

  return (
    <header className="app-header">
      <div className="brand-lockup">
        <img className="brand-mark" src="/favicon.svg" alt="" width={32} height={32} />
        <h1>CaptionNest</h1>
      </div>

      <div className="header-status" aria-label="运行环境状态">
        <button
          type="button"
          className="status-item status-button"
          onClick={onRefresh}
          title="重新检查本地服务"
        >
          <span className={`status-dot ${connected ? 'is-online' : 'is-offline'}`} />
          <span className="status-primary">本地环境</span>
          <span className="sr-only">：{statusLabel}，点击重新检查</span>
        </button>
        <span className="status-divider" aria-hidden="true" />
        <span className={`status-item ${cudaAvailable ? 'is-cuda' : ''}`}>
          <Cpu size={16} aria-hidden="true" />
          {cudaAvailable ? 'CUDA 可用' : 'CPU 模式'}
        </span>
        <span className="status-divider" aria-hidden="true" />
        <span className="status-item">
          <Settings size={18} aria-hidden="true" />
          设置
        </span>
      </div>
    </header>
  )
}
