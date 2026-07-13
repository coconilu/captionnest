export type SourceLanguage = 'auto' | 'ja' | 'en'
export type AsrModel = 'large-v3' | 'large-v3-turbo'
export type TranslationProvider = 'codex_spark' | 'lmstudio' | 'deepseek'
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type JobStage =
  | 'queued'
  | 'extracting'
  | 'transcribing'
  | 'translating'
  | 'writing'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface UploadResponse {
  upload_id: string
  name: string
  path: string
  size: number
}

export interface PickVideoResponse {
  selected: boolean
  path: string | null
  name?: string | null
  size?: number | null
}

export interface JobLog {
  timestamp?: string
  level?: 'info' | 'success' | 'warning' | 'error' | string
  message: string
}

export interface JobView {
  id: string
  status: JobStatus
  stage: JobStage
  progress: number
  source_name: string
  source_kind: 'path' | 'upload'
  source_language: string
  detected_language: string | null
  translation_provider: TranslationProvider
  created_at: string
  updated_at: string
  source_subtitle_path: string | null
  translated_subtitle_path: string | null
  error: string | null
  logs: JobLog[]
}

export interface BackendHealth {
  status?: string
  [key: string]: unknown
}

export interface BackendCapabilities {
  asr?: {
    provider?: string
    installed?: boolean
    cuda_available?: boolean
    models?: string[]
    languages?: string[]
  }
  translation?: {
    providers?: Array<{
      id: TranslationProvider
      default_model?: string
      default_endpoint?: string
      key_required?: boolean
    }>
  }
  tools?: {
    ffmpeg?: boolean
    codex?: boolean
    nvidia_smi?: boolean
    system_file_picker?: boolean
  }
  video_extensions?: string[]
  [key: string]: unknown
}

export interface JobRequest {
  video_path?: string
  upload_id?: string
  source_language: SourceLanguage
  asr: {
    model: AsrModel
    device: 'cuda' | 'cpu'
    compute_type: 'float16' | 'int8'
    vad_filter: boolean
    beam_size: number
  }
  translation: {
    provider: TranslationProvider
    model?: string
    endpoint?: string
    api_key?: string
  }
  output: {
    write_source_srt: boolean
  }
}
