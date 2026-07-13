import type {
  BackendCapabilities,
  BackendHealth,
  JobRequest,
  JobView,
  PickVideoResponse,
  UploadResponse,
} from '../types/api'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

async function readError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string; message?: string }
    return payload.detail ?? payload.message ?? `请求失败（${response.status}）`
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

export function uploadVideo(file: File, signal?: AbortSignal) {
  const form = new FormData()
  form.append('file', file)
  return request<UploadResponse>('/api/uploads', { method: 'POST', body: form, signal })
}

export function pickVideo(signal?: AbortSignal) {
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
