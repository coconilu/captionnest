import type {
  BatchCreateRequest,
  BatchCreateResult,
  BatchPreflightRequest,
  BatchPreflightResult,
  BatchRecord,
  BackendCapabilities,
  BackendHealth,
  BulkUploadResponse,
  EnvironmentView,
  JobBulkActionRequest,
  JobBulkActionResponse,
  JobRequest,
  JobRunRequest,
  JobStatus,
  JobStepConfig,
  JobStepId,
  JobSummaryPage,
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

export async function pickVideos(signal?: AbortSignal): Promise<PickVideoResponse[]> {
  try {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selected = await open({
      multiple: true,
      directory: false,
      filters: [{ name: '视频文件', extensions: ['mp4', 'mkv', 'mov', 'avi', 'webm', 'm4v', 'ts', 'mts', 'm2ts'] }],
    })
    const paths = Array.isArray(selected)
      ? selected
      : typeof selected === 'string'
        ? [selected]
        : []
    return paths.map((path) => ({ selected: true, path }))
  } catch {
    // Normal browser development has no Tauri IPC; the system picker remains a single-path fallback.
  }
  const selected = await request<PickVideoResponse>('/api/system/pick-video', {
    method: 'POST',
    signal,
  })
  return selected.selected && selected.path ? [selected] : []
}

export function uploadFiles(files: File[], signal?: AbortSignal) {
  const body = new FormData()
  files.forEach((file) => body.append('files', file, file.name))
  return request<BulkUploadResponse>('/api/uploads/bulk', {
    method: 'POST',
    body,
    signal,
  })
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

export function listJobs(signal?: AbortSignal) {
  return request<JobView[]>('/api/jobs', { signal })
}

export interface JobSummaryQuery {
  cursor?: string
  limit?: number
  statuses?: JobStatus[]
  batchId?: string
  query?: string
  updatedAfter?: string
}

export function listJobSummaries(query: JobSummaryQuery = {}, signal?: AbortSignal) {
  const params = new URLSearchParams()
  params.set('limit', String(query.limit ?? 100))
  if (query.cursor) params.set('cursor', query.cursor)
  query.statuses?.forEach((status) => params.append('status', status))
  if (query.batchId) params.set('batch_id', query.batchId)
  if (query.query) params.set('q', query.query)
  if (query.updatedAfter) params.set('updated_after', query.updatedAfter)
  return request<JobSummaryPage>(`/api/jobs?${params.toString()}`, { signal })
}

export function listBatches(signal?: AbortSignal) {
  return request<BatchRecord[]>('/api/batches', { signal })
}

export function preflightBatch(payload: BatchPreflightRequest, signal?: AbortSignal) {
  return request<BatchPreflightResult>('/api/batches/preflight', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function createBatch(payload: BatchCreateRequest, signal?: AbortSignal) {
  return request<BatchCreateResult>('/api/batches', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function runBulkJobAction(payload: JobBulkActionRequest, signal?: AbortSignal) {
  return request<JobBulkActionResponse>('/api/jobs/bulk-actions', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function runJob(id: string, payload: JobRunRequest = {}, signal?: AbortSignal) {
  return request<JobView>(`/api/jobs/${encodeURIComponent(id)}/run`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function cancelJob(id: string, signal?: AbortSignal) {
  return request<JobView>(`/api/jobs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    signal,
  })
}

export function updateJobStepConfig(
  id: string,
  step: JobStepId,
  config: JobStepConfig,
  signal?: AbortSignal,
) {
  return request<JobView>(
    `/api/jobs/${encodeURIComponent(id)}/steps/${encodeURIComponent(step)}/config`,
    {
      method: 'PATCH',
      body: JSON.stringify({ config }),
      signal,
    },
  )
}

export function runJobStep(
  id: string,
  step: JobStepId,
  payload: JobRunRequest = {},
  signal?: AbortSignal,
) {
  return request<JobView>(
    `/api/jobs/${encodeURIComponent(id)}/steps/${encodeURIComponent(step)}/run`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
      signal,
    },
  )
}

export function deleteJob(id: string, signal?: AbortSignal) {
  return request<{ deleted: boolean; job_id: string }>(`/api/jobs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    signal,
  })
}

export function openFolder(path: string) {
  return request<{ opened: boolean; path: string }>('/api/system/open-folder', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}
