import { isTauri } from '@tauri-apps/api/core'

export type DesktopVersionState =
  | { status: 'loading'; version: null }
  | { status: 'browser'; version: null }
  | { status: 'error'; version: null }
  | { status: 'ready'; version: string }

export interface DesktopVersionRuntime {
  isDesktop: () => boolean
  getDesktopVersion: () => Promise<string>
}

export interface VersionDisplay {
  desktopText: string
  sidecarText: string
  notice: string | null
  noticeTone: 'info' | 'warning' | null
}

interface VersionDisplayInput {
  desktop: DesktopVersionState
  sidecarConnected: boolean
  sidecarChecking: boolean
  sidecarVersion: string | null
}

const defaultRuntime: DesktopVersionRuntime = {
  isDesktop: isTauri,
  getDesktopVersion: async () => {
    const { getVersion } = await import('@tauri-apps/api/app')
    return getVersion()
  },
}

function cleanVersion(version: string | null): string | null {
  const value = version?.trim()
  return value ? value : null
}

function versionLabel(version: string): string {
  return version.toLowerCase().startsWith('v') ? version : `v${version}`
}

export async function readDesktopVersion(
  runtime: DesktopVersionRuntime = defaultRuntime,
): Promise<DesktopVersionState> {
  if (!runtime.isDesktop()) {
    return { status: 'browser', version: null }
  }

  try {
    const version = cleanVersion(await runtime.getDesktopVersion())
    return version
      ? { status: 'ready', version }
      : { status: 'error', version: null }
  } catch {
    return { status: 'error', version: null }
  }
}

export function buildVersionDisplay({
  desktop,
  sidecarConnected,
  sidecarChecking,
  sidecarVersion,
}: VersionDisplayInput): VersionDisplay {
  const cleanSidecarVersion = cleanVersion(sidecarVersion)
  const desktopText = desktop.status === 'loading'
    ? '正在读取…'
    : desktop.status === 'browser'
      ? '浏览器开发模式'
      : desktop.status === 'error'
        ? '版本读取失败'
        : versionLabel(desktop.version)
  const sidecarText = sidecarChecking
    ? '正在连接…'
    : !sidecarConnected
      ? '未连接'
      : cleanSidecarVersion
        ? versionLabel(cleanSidecarVersion)
        : '版本信息不可用'

  if (
    desktop.status === 'ready'
    && sidecarConnected
    && cleanSidecarVersion
    && desktop.version !== cleanSidecarVersion
  ) {
    return {
      desktopText,
      sidecarText,
      notice: `检测到组件版本不一致：桌面应用 ${versionLabel(desktop.version)}，Sidecar ${versionLabel(cleanSidecarVersion)}。请重新安装完整版本，避免组件混用。`,
      noticeTone: 'warning',
    }
  }

  if (desktop.status === 'browser') {
    return {
      desktopText,
      sidecarText,
      notice: '当前为浏览器开发模式，桌面应用版本不可读取。',
      noticeTone: 'info',
    }
  }

  if (desktop.status === 'error') {
    return {
      desktopText,
      sidecarText,
      notice: '无法读取桌面运行时版本，请在 CaptionNest 桌面应用中重试。',
      noticeTone: 'warning',
    }
  }

  return { desktopText, sidecarText, notice: null, noticeTone: null }
}
