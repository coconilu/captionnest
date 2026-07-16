export type TargetLanguage = 'zh-CN' | 'en' | 'ko'
export type AsrProvider = 'faster_whisper' | 'qwen3_asr'
export type AsrModel = 'small' | 'medium' | 'large-v3-turbo' | 'large-v3' | 'qwen3-asr-1.7b'
export type AsrOutputMode = 'chunk_segments' | 'word_resegmented'
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
  detected_language: string | null
  target_language: TargetLanguage
  asr_provider: AsrProvider
  translation_provider: TranslationProvider
  created_at: string
  updated_at: string
  subtitle_path: string | null
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
    providers?: Array<{
      id: AsrProvider
      label: string
      installed: boolean
      cuda_available: boolean
      models: AsrModel[]
    }>
    source_language?: 'auto'
    target_languages?: TargetLanguage[]
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
    media_decoder?: boolean
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
  target_language: TargetLanguage
  asr: {
    provider: AsrProvider
    model: AsrModel
    device: 'cuda' | 'cpu'
    compute_type: 'float16' | 'int8'
    vad_filter: boolean
    beam_size: number
    output_mode: AsrOutputMode
  }
  translation: {
    provider: TranslationProvider
    model?: string
    endpoint?: string
    api_key?: string
  }
}

export type EnvironmentComponentStatus = 'ready' | 'missing' | 'broken' | 'failed'
export type ModelStatus = 'ready' | 'missing' | 'downloading' | 'damaged'
export type CodexStatus = 'not_installed' | 'not_logged_in' | 'ready' | 'check_failed'

export interface EnvironmentView {
  runtime: {
    status: 'ready' | 'failed'
    version: string | null
    message: string | null
  }
  asr: {
    status: EnvironmentComponentStatus
    provider: string | null
    version: string | null
    message: string | null
  }
  model: {
    status: ModelStatus
    name: string | null
    path: string | null
    message: string | null
  }
  acceleration: {
    status: 'cpu' | 'cuda_ready' | 'cuda_unavailable'
    device: 'cpu' | 'cuda'
    cuda_available: boolean
    message: string | null
  }
  codex: {
    status: CodexStatus
    version: string | null
    install_url: string | null
    message: string | null
  }
  tools: {
    media: {
      status: EnvironmentComponentStatus
      provider: string | null
      version: string | null
      message: string | null
    }
  }
}

export interface ModelItem {
  id: string
  label: string
  provider: AsrProvider
  status: ModelStatus
  path: string | null
  message: string | null
  progress: number | null
  recommended_for: 'cpu' | 'cuda' | 'quality'
}

export interface ModelCatalog {
  items: ModelItem[]
  model_root: string
}
