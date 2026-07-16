import { useEffect, useState } from 'react'

import { DEFAULT_SETTINGS, type SettingsValue } from '../types/settings'

const STORAGE_KEY = 'captionnest.settings.v1'
const TARGET_LANGUAGES = new Set(['zh-CN', 'en', 'ko'])
const ASR_MODELS = new Set([
  'small',
  'medium',
  'large-v3-turbo',
  'large-v3',
])
const OUTPUT_MODES = new Set(['chunk_segments', 'word_resegmented'])
const TRANSLATION_PROVIDERS = new Set(['codex_spark', 'lmstudio', 'deepseek'])

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function numberValue(value: unknown, fallback: number, minimum: number, maximum: number): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(minimum, Math.min(maximum, value))
    : fallback
}

function loadSettings(): SettingsValue {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS
  try {
    const stored = objectValue(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? 'null'))
    if (!stored || stored.version !== 1) return DEFAULT_SETTINGS
    const value = objectValue(stored.settings)
    if (!value) return DEFAULT_SETTINGS

    const targetLanguage = stringValue(value.targetLanguage, DEFAULT_SETTINGS.targetLanguage)
    const asrModel = stringValue(value.asrModel, DEFAULT_SETTINGS.asrModel)
    const asrOutputMode = stringValue(value.asrOutputMode, DEFAULT_SETTINGS.asrOutputMode)
    const provider = stringValue(value.provider, DEFAULT_SETTINGS.provider)
    return {
      ...DEFAULT_SETTINGS,
      targetLanguage: TARGET_LANGUAGES.has(targetLanguage)
        ? targetLanguage as SettingsValue['targetLanguage']
        : DEFAULT_SETTINGS.targetLanguage,
      asrModel: ASR_MODELS.has(asrModel)
        ? asrModel as SettingsValue['asrModel']
        : DEFAULT_SETTINGS.asrModel,
      asrOutputMode: OUTPUT_MODES.has(asrOutputMode)
        ? asrOutputMode as SettingsValue['asrOutputMode']
        : DEFAULT_SETTINGS.asrOutputMode,
      useCuda: booleanValue(value.useCuda, DEFAULT_SETTINGS.useCuda),
      asrVadFilter: booleanValue(value.asrVadFilter, DEFAULT_SETTINGS.asrVadFilter),
      asrDynamicChunking: booleanValue(
        value.asrDynamicChunking,
        DEFAULT_SETTINGS.asrDynamicChunking,
      ),
      asrSelectiveRetry: booleanValue(
        value.asrSelectiveRetry,
        DEFAULT_SETTINGS.asrSelectiveRetry,
      ),
      asrBeamSize: numberValue(value.asrBeamSize, DEFAULT_SETTINGS.asrBeamSize, 1, 20),
      provider: TRANSLATION_PROVIDERS.has(provider)
        ? provider as SettingsValue['provider']
        : DEFAULT_SETTINGS.provider,
      translationTimeoutSeconds: numberValue(
        value.translationTimeoutSeconds,
        DEFAULT_SETTINGS.translationTimeoutSeconds,
        10,
        3600,
      ),
      lmstudioEndpoint: stringValue(
        value.lmstudioEndpoint,
        DEFAULT_SETTINGS.lmstudioEndpoint,
      ),
      lmstudioModel: stringValue(value.lmstudioModel, DEFAULT_SETTINGS.lmstudioModel),
      deepseekEndpoint: stringValue(
        value.deepseekEndpoint,
        DEFAULT_SETTINGS.deepseekEndpoint,
      ),
      deepseekModel: stringValue(value.deepseekModel, DEFAULT_SETTINGS.deepseekModel),
      deepseekApiKey: '',
      exportOutputDirectory: stringValue(
        value.exportOutputDirectory,
        DEFAULT_SETTINGS.exportOutputDirectory,
      ),
      exportOverwriteExisting: booleanValue(
        value.exportOverwriteExisting,
        DEFAULT_SETTINGS.exportOverwriteExisting,
      ),
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

function persistedValue(settings: SettingsValue) {
  return {
    version: 1,
    settings: {
      targetLanguage: settings.targetLanguage,
      asrModel: settings.asrModel,
      asrOutputMode: settings.asrOutputMode,
      useCuda: settings.useCuda,
      asrVadFilter: settings.asrVadFilter,
      asrDynamicChunking: settings.asrDynamicChunking,
      asrSelectiveRetry: settings.asrSelectiveRetry,
      asrBeamSize: settings.asrBeamSize,
      provider: settings.provider,
      translationTimeoutSeconds: settings.translationTimeoutSeconds,
      lmstudioEndpoint: settings.lmstudioEndpoint,
      lmstudioModel: settings.lmstudioModel,
      deepseekEndpoint: settings.deepseekEndpoint,
      deepseekModel: settings.deepseekModel,
      exportOutputDirectory: settings.exportOutputDirectory,
      exportOverwriteExisting: settings.exportOverwriteExisting,
    },
  }
}

export function usePersistedSettings() {
  const [settings, setSettings] = useState<SettingsValue>(loadSettings)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(persistedValue(settings)))
    } catch {
      // Preferences are a convenience. A blocked WebView store must not prevent processing.
    }
  }, [settings])

  return [settings, setSettings] as const
}
