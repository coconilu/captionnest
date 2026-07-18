import { describe, expect, it, vi } from 'vitest'

import { buildVersionDisplay, readDesktopVersion } from './appVersion'

describe('readDesktopVersion', () => {
  it('reads the version from the desktop runtime', async () => {
    await expect(readDesktopVersion({
      isDesktop: () => true,
      getDesktopVersion: async () => ' 0.2.4 ',
    })).resolves.toEqual({ status: 'ready', version: '0.2.4' })
  })

  it('does not invoke the Tauri version API in an ordinary browser', async () => {
    const getDesktopVersion = vi.fn(async () => '0.2.4')

    await expect(readDesktopVersion({
      isDesktop: () => false,
      getDesktopVersion,
    })).resolves.toEqual({ status: 'browser', version: null })
    expect(getDesktopVersion).not.toHaveBeenCalled()
  })

  it('returns a deterministic fallback when the runtime read fails', async () => {
    await expect(readDesktopVersion({
      isDesktop: () => true,
      getDesktopVersion: async () => { throw new Error('runtime unavailable') },
    })).resolves.toEqual({ status: 'error', version: null })
  })
})

describe('buildVersionDisplay', () => {
  it('shows equal versions without a warning', () => {
    const display = buildVersionDisplay({
      desktop: { status: 'ready', version: '0.2.4' },
      sidecarConnected: true,
      sidecarChecking: false,
      sidecarVersion: '0.2.4',
    })

    expect(display).toMatchObject({
      desktopText: 'v0.2.4',
      sidecarText: 'v0.2.4',
      notice: null,
    })
  })

  it('shows both actual versions in the mismatch warning', () => {
    const display = buildVersionDisplay({
      desktop: { status: 'ready', version: '0.2.4' },
      sidecarConnected: true,
      sidecarChecking: false,
      sidecarVersion: '0.2.5',
    })

    expect(display.noticeTone).toBe('warning')
    expect(display.notice).toContain('桌面应用 v0.2.4')
    expect(display.notice).toContain('Sidecar v0.2.5')
  })

  it('shows the offline Sidecar fallback without claiming a mismatch', () => {
    const display = buildVersionDisplay({
      desktop: { status: 'ready', version: '0.2.4' },
      sidecarConnected: false,
      sidecarChecking: false,
      sidecarVersion: null,
    })

    expect(display.sidecarText).toBe('未连接')
    expect(display.notice).toBeNull()
  })

  it('shows a browser development fallback without a mismatch warning', () => {
    const display = buildVersionDisplay({
      desktop: { status: 'browser', version: null },
      sidecarConnected: true,
      sidecarChecking: false,
      sidecarVersion: '0.2.4',
    })

    expect(display.desktopText).toBe('浏览器开发模式')
    expect(display.noticeTone).toBe('info')
    expect(display.notice).not.toContain('版本不一致')
  })
})
