import type {
  AsrModel,
  AsrOutputMode,
  TargetLanguage,
  TranslationProvider,
} from './api'

export interface SettingsValue {
  targetLanguage: TargetLanguage
  asrModel: AsrModel
  asrOutputMode: AsrOutputMode
  useCuda: boolean
  asrVadFilter: boolean
  asrDynamicChunking: boolean
  asrSelectiveRetry: boolean
  asrTimestampNormalization: boolean
  asrBeamSize: number
  asrHotwordsText: string
  provider: TranslationProvider
  translationTimeoutSeconds: number
  lmstudioEndpoint: string
  lmstudioModel: string
  deepseekEndpoint: string
  deepseekModel: string
  deepseekApiKey: string
  exportOutputDirectory: string
  exportOverwriteExisting: boolean
}
export const DEFAULT_SETTINGS: SettingsValue = {
  targetLanguage: 'zh-CN',
  asrModel: 'small',
  asrOutputMode: 'word_resegmented',
  useCuda: true,
  asrVadFilter: true,
  asrDynamicChunking: true,
  asrSelectiveRetry: true,
  asrTimestampNormalization: false,
  asrBeamSize: 5,
  asrHotwordsText: '',
  provider: 'codex_spark',
  translationTimeoutSeconds: 300,
  lmstudioEndpoint: 'http://127.0.0.1:1234/v1',
  lmstudioModel: '',
  deepseekEndpoint: 'https://api.deepseek.com',
  deepseekModel: 'deepseek-v4-flash',
  deepseekApiKey: '',
  exportOutputDirectory: '',
  exportOverwriteExisting: true,
}
