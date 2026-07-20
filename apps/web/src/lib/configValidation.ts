import type {
  CodexStatus,
  EnvironmentView,
  ModelStatus,
  TranslationProvider,
} from '../types/api'

export type ConfigValidationKind =
  | 'hotwords'
  | 'environment-checking'
  | 'environment-failed'
  | 'asr-runtime-missing'
  | 'asr-unavailable'
  | 'media-unavailable'
  | 'model-status-unknown'
  | 'model-missing'
  | 'model-damaged'
  | 'model-downloading'
  | 'codex-not-installed'
  | 'codex-not-logged-in'
  | 'codex-check-failed'
  | 'lmstudio-model-missing'

export interface ConfigValidationResult {
  kind: ConfigValidationKind
  message: string
}

export interface ConfigValidationInput {
  hotwordError: string | null
  environmentChecking: boolean
  modelsChecking: boolean
  environmentError: string | null
  asrCapability: { label: string; installed: boolean } | null | undefined
  environment: EnvironmentView | null | undefined
  selectedModelStatus: ModelStatus | undefined
  modelsError: string | null
  provider: TranslationProvider
  codexStatus: CodexStatus | undefined
  lmstudioModel: string
}

export function resolveConfigValidation(
  input: ConfigValidationInput,
): ConfigValidationResult | null {
  if (input.hotwordError) return { kind: 'hotwords', message: input.hotwordError }
  if (input.environmentChecking || input.modelsChecking) {
    return { kind: 'environment-checking', message: '正在检测运行环境' }
  }
  if (input.environmentError) {
    return { kind: 'environment-failed', message: '运行环境检测失败，请刷新检测' }
  }
  if (input.asrCapability && !input.asrCapability.installed) {
    return {
      kind: 'asr-runtime-missing',
      message: `${input.asrCapability.label} 运行时尚未安装`,
    }
  }
  if (!input.asrCapability && input.environment?.asr.status !== 'ready') {
    return {
      kind: 'asr-unavailable',
      message: input.environment?.asr.message ?? '语音识别组件不可用',
    }
  }
  if (input.environment?.tools.media.status !== 'ready') {
    return {
      kind: 'media-unavailable',
      message: input.environment?.tools.media.message ?? '媒体解码组件不可用',
    }
  }
  if (!input.selectedModelStatus) {
    return {
      kind: 'model-status-unknown',
      message: input.modelsError ?? '无法获取识别模型状态，请刷新检测',
    }
  }
  if (input.selectedModelStatus === 'missing') {
    return { kind: 'model-missing', message: '请先下载识别模型' }
  }
  if (input.selectedModelStatus === 'damaged') {
    return { kind: 'model-damaged', message: '识别模型已损坏，请重新下载' }
  }
  if (input.selectedModelStatus === 'downloading') {
    return { kind: 'model-downloading', message: '识别模型正在下载，进度会自动更新' }
  }
  if (input.provider === 'codex_spark' && input.codexStatus === 'not_installed') {
    return { kind: 'codex-not-installed', message: '请先安装 Codex 并刷新检测' }
  }
  if (input.provider === 'codex_spark' && input.codexStatus === 'not_logged_in') {
    return { kind: 'codex-not-logged-in', message: '请先完成 Codex 登录并刷新检测' }
  }
  if (input.provider === 'codex_spark' && input.codexStatus === 'check_failed') {
    return { kind: 'codex-check-failed', message: 'Codex 状态检测失败，请刷新重试' }
  }
  if (input.provider === 'lmstudio' && !input.lmstudioModel.trim()) {
    return { kind: 'lmstudio-model-missing', message: '请填写 LM Studio 模型 ID' }
  }
  return null
}

export function isModelDownloadBlock(result: ConfigValidationResult | null): boolean {
  return result?.kind === 'model-missing' || result?.kind === 'model-damaged'
}
