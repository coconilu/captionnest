import type {
  BackendCapabilities,
  BackendHealth,
  EnvironmentView,
  JobRequest,
  JobView,
  ModelCatalog,
  ModelItem,
  PickVideoResponse,
} from '../types/api'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

async function readError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: string | Array<{ msg?: string }>
      message?: string
    }
    if (typeof payload.detail === 'string') return payload.detail
    if (Array.isArray(payload.detail)) {
      const messages = payload.detail
        .map((item) => item.msg)
        .filter((message): message is string => Boolean(message))
      if (messages.length) return messages.join('；')
    }
    return payload.message ?? `请求失败（${response.status}）`
  } catch {
    return `请求失败（${response.status} ${response.statusText}）`
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as T
}

export function getHealth(signal?: AbortSignal) {
  return request<BackendHealth>('/api/health', { signal })
}

export function getCapabilities(signal?: AbortSignal) {
  return request<BackendCapabilities>('/api/capabilities', { signal })
}

export function getEnvironment(signal?: AbortSignal) {
  return request<EnvironmentView>('/api/environment', { signal })
}

export function getModels(signal?: AbortSignal) {
  return request<ModelCatalog>('/api/models', { signal })
}

export function downloadModel(id: string, signal?: AbortSignal) {
  return request<ModelItem>(`/api/models/${encodeURIComponent(id)}/download`, {
    method: 'POST',
    signal,
  })
}

export async function pickVideo(signal?: AbortSignal): Promise<PickVideoResponse> {
  try {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: '视频文件', extensions: ['mp4', 'mkv', 'mov', 'avi', 'webm', 'm4v', 'ts', 'mts', 'm2ts'] }],
    })
    if (typeof selected === 'string') return { selected: true, path: selected }
    if (selected === null) return { selected: false, path: null }
  } catch {
    // Normal browser development has no Tauri IPC; use the local API picker there.
  }
  return request<PickVideoResponse>('/api/system/pick-video', { method: 'POST', signal })
}

export function createJob(payload: JobRequest, signal?: AbortSignal) {
  return request<JobView>('/api/jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function getJob(id: string, signal?: AbortSignal) {
  return request<JobView>(`/api/jobs/${encodeURIComponent(id)}`, { signal })
}

export function openFolder(path: string) {
  return request<{ opened: boolean; path: string }>('/api/system/open-folder', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}
