export type TargetLanguage = 'zh-CN' | 'en' | 'ko'
export type AsrProvider = 'faster_whisper'
export type AsrModel = 'small' | 'medium' | 'large-v3-turbo' | 'large-v3'
export type LegacyAsrProvider = 'qwen3_asr'
export type LegacyAsrModel = 'qwen3-asr-1.7b'
export type JobAsrProvider = AsrProvider | LegacyAsrProvider
export type JobAsrModel = AsrModel | LegacyAsrModel
export type AsrOutputMode = 'chunk_segments' | 'word_resegmented'
export type TranslationProvider = 'codex_spark' | 'lmstudio' | 'deepseek'
export type JobStatus =
  | 'draft'
  | 'queued'
  | 'running'
  | 'waiting_for_input'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
export type QueueStatus = JobStatus
export type JobStage =
  | 'draft'
  | 'queued'
  | 'waiting_for_input'
  | 'extracting'
  | 'transcribing'
  | 'translating'
  | 'writing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
export type JobStepId = 'media' | 'transcription' | 'translation' | 'export'
export type StepStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'stale'
  | 'cancelled'
  | 'interrupted'

export interface MediaStepConfig {
  source_kind: 'path' | 'upload'
  path: string
  name: string
}

export interface AsrStepConfig {
  provider: JobAsrProvider
  model: JobAsrModel
  device: 'auto' | 'cuda' | 'cpu'
  compute_type: string
  vad_filter: boolean
  dynamic_chunking?: boolean
  selective_retry?: boolean
  timestamp_normalization?: boolean
  beam_size: number
  output_mode: AsrOutputMode
  hotwords?: string[]
}

export interface TranslationStepConfig {
  target_language: TargetLanguage
  provider: TranslationProvider
  model: string | null
  endpoint: string | null
  timeout_seconds: number
}

export interface ExportStepConfig {
  output_directory: string | null
  overwrite_existing: boolean
  format: 'srt'
  bilingual_order: 'source_then_translation'
}

export type JobStepConfig =
  | MediaStepConfig
  | AsrStepConfig
  | TranslationStepConfig
  | ExportStepConfig

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

export interface StepArtifact {
  id: string
  step: JobStepId
  path: string
  fingerprint: string
  config_fingerprint: string
  input_fingerprints: Record<string, string>
  created_at: string
  summary: Record<string, unknown>
}

export interface ModelUsageSummary {
  provider: string
  model: string | null
  request_count: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cached_input_tokens: number | null
  reasoning_tokens: number | null
  source: 'provider' | 'cli' | 'unavailable' | 'mixed'
  complete: boolean
}

export interface StepAttempt {
  number: number
  status: StepStatus
  config: Record<string, unknown>
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  model_usage: ModelUsageSummary | null
  artifact_id: string | null
  error: string | null
}

export interface JobStepView {
  id: JobStepId
  status: StepStatus
  progress: number
  config_revision: number
  config: JobStepConfig
  attempts: StepAttempt[]
  artifact: StepArtifact | null
  error: string | null
  can_run: boolean
  latest_duration_ms: number | null
  total_duration_ms: number | null
  total_model_usage: ModelUsageSummary | null
}

export interface JobView {
  id: string
  batch_id: string | null
  status: JobStatus
  queue_status: QueueStatus
  queue_position: number | null
  priority: number
  stage: JobStage
  progress: number
  current_step: JobStepId | null
  source_name: string
  source_kind: 'path' | 'upload'
  detected_language: string | null
  target_language: TargetLanguage
  asr_provider: JobAsrProvider
  translation_provider: TranslationProvider
  created_at: string
  updated_at: string
  interrupted_at: string | null
  subtitle_path: string | null
  error: string | null
  logs: JobLog[]
  steps: JobStepView[]
  wall_duration_ms: number | null
  cumulative_attempt_duration_ms: number | null
  total_model_usage: ModelUsageSummary | null
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
    dynamic_chunking: boolean
    selective_retry: boolean
    timestamp_normalization: boolean
    beam_size: number
    output_mode: AsrOutputMode
    hotwords: string[]
  }
  translation: {
    provider: TranslationProvider
    model?: string
    endpoint?: string
    timeout_seconds?: number
  }
  export?: ExportStepConfig
  auto_start?: boolean
}

export interface JobRunRequest {
  api_key?: string
  continue_pipeline?: boolean
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
