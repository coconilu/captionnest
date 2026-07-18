import { buildVersionDisplay, type DesktopVersionState } from '../lib/appVersion'

interface AboutPanelProps {
  desktopVersion: DesktopVersionState
  sidecarConnected: boolean
  sidecarChecking: boolean
  sidecarVersion: string | null
}

export function AboutPanel({
  desktopVersion,
  sidecarConnected,
  sidecarChecking,
  sidecarVersion,
}: AboutPanelProps) {
  const display = buildVersionDisplay({
    desktop: desktopVersion,
    sidecarConnected,
    sidecarChecking,
    sidecarVersion,
  })

  return (
    <section className="about-panel" aria-labelledby="about-panel-title">
      <header className="about-panel-header">
        <h3 id="about-panel-title">关于 CaptionNest</h3>
        <p>版本信息来自当前运行的桌面应用与本地服务。</p>
      </header>
      <dl className="about-version-list" aria-live="polite">
        <div className="about-version-row">
          <dt>桌面应用</dt>
          <dd>{display.desktopText}</dd>
        </div>
        <div className="about-version-row">
          <dt>本地 Sidecar</dt>
          <dd>{display.sidecarText}</dd>
        </div>
      </dl>
      {display.notice ? (
        <p
          className={`about-version-notice ${display.noticeTone === 'warning' ? 'is-warning' : ''}`}
          role={display.noticeTone === 'warning' ? 'alert' : 'status'}
        >
          {display.notice}
        </p>
      ) : null}
    </section>
  )
}
