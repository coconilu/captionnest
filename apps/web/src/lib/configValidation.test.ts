import { describe, expect, it } from 'vitest'

import type { EnvironmentView } from '../types/api'
import {
  isModelDownloadBlock,
  resolveConfigValidation,
  type ConfigValidationInput,
} from './configValidation'

const READY_ENVIRONMENT = {
  asr: { status: 'ready', provider: null, version: null, message: null },
  tools: { media: { status: 'ready', provider: null, version: null, message: null } },
} as unknown as EnvironmentView

function baseInput(overrides: Partial<ConfigValidationInput> = {}): ConfigValidationInput {
  return {
    hotwordError: null,
    environmentChecking: false,
    modelsChecking: false,
    environmentError: null,
    asrCapability: { label: 'faster-whisper', installed: true },
    environment: READY_ENVIRONMENT,
    selectedModelStatus: 'ready',
    modelsError: null,
    provider: 'deepseek',
    codexStatus: 'ready',
    lmstudioModel: 'qwen3-30b-a3b',
    ...overrides,
  }
}

describe('resolveConfigValidation', () => {
  it('returns null when every check passes', () => {
    expect(resolveConfigValidation(baseInput())).toBeNull()
  })

  it('reports hotword errors ahead of a missing model (no download action)', () => {
    const result = resolveConfigValidation(baseInput({
      hotwordError: '单个提示词不能超过 64 个字符（第 1 项）',
      selectedModelStatus: 'missing',
    }))
    expect(result?.kind).toBe('hotwords')
    expect(result?.message).toContain('单个提示词不能超过')
    expect(isModelDownloadBlock(result)).toBe(false)
  })

  it('reports a missing model with the download action when nothing else blocks', () => {
    const result = resolveConfigValidation(baseInput({ selectedModelStatus: 'missing' }))
    expect(result).toEqual({ kind: 'model-missing', message: '请先下载识别模型' })
    expect(isModelDownloadBlock(result)).toBe(true)
  })

  it('reports a damaged model with the download action when nothing else blocks', () => {
    const result = resolveConfigValidation(baseInput({ selectedModelStatus: 'damaged' }))
    expect(result?.kind).toBe('model-damaged')
    expect(isModelDownloadBlock(result)).toBe(true)
  })

  it('keeps environment checks ahead of model download blocks', () => {
    const checking = resolveConfigValidation(baseInput({
      environmentChecking: true,
      selectedModelStatus: 'missing',
    }))
    expect(checking?.kind).toBe('environment-checking')
    expect(isModelDownloadBlock(checking)).toBe(false)

    const failed = resolveConfigValidation(baseInput({
      environmentError: 'sidecar unreachable',
      selectedModelStatus: 'damaged',
    }))
    expect(failed?.kind).toBe('environment-failed')
    expect(isModelDownloadBlock(failed)).toBe(false)
  })

  it('treats a downloading model as informational, not a download action', () => {
    const result = resolveConfigValidation(baseInput({ selectedModelStatus: 'downloading' }))
    expect(result?.kind).toBe('model-downloading')
    expect(isModelDownloadBlock(result)).toBe(false)
  })

  it('keeps the codex and lmstudio checks behind the model checks', () => {
    const codex = resolveConfigValidation(baseInput({
      provider: 'codex_spark',
      codexStatus: 'not_logged_in',
    }))
    expect(codex?.kind).toBe('codex-not-logged-in')

    const lmstudio = resolveConfigValidation(baseInput({
      provider: 'lmstudio',
      lmstudioModel: '  ',
    }))
    expect(lmstudio?.kind).toBe('lmstudio-model-missing')
  })
})
