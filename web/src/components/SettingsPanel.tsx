import { Eye, EyeOff, Play, ShieldCheck } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import type { AsrModel, TargetLanguage, TranslationProvider } from '../types/api'

export interface SettingsValue {
  targetLanguage: TargetLanguage
  asrModel: AsrModel
  useCuda: boolean
  provider: TranslationProvider
  lmstudioEndpoint: string
  lmstudioModel: string
  deepseekEndpoint: string
  deepseekModel: string
  deepseekApiKey: string
}

interface SettingsPanelProps {
  value: SettingsValue
  cudaAvailable?: boolean
  disabled: boolean
  canStart: boolean
  startHint: string
  children?: ReactNode
  onChange: (next: SettingsValue) => void
  onStart: () => void
}

const PROVIDERS: Array<{ value: TranslationProvider; label: string }> = [
  { value: 'codex_spark', label: 'Codex Spark' },
  { value: 'lmstudio', label: 'LM Studio' },
  { value: 'deepseek', label: 'DeepSeek' },
]

const TARGET_LANGUAGES: Array<{ value: TargetLanguage; label: string }> = [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en', label: '英语' },
  { value: 'ko', label: '韩语' },
]

export function SettingsPanel({
  value,
  cudaAvailable,
  disabled,
  canStart,
  startHint,
  children,
  onChange,
  onStart,
}: SettingsPanelProps) {
  const [showKey, setShowKey] = useState(false)
  const patch = <K extends keyof SettingsValue>(key: K, next: SettingsValue[K]) =>
    onChange({ ...value, [key]: next })

  return (
    <aside className="settings-sidebar" aria-label="字幕任务设置">
      <section className="settings-section">
        <div className="section-heading section-heading-with-copy">
          <div>
            <h2>识别设置</h2>
            <span>源语言将自动检测</span>
          </div>
        </div>
        <label className="field">
          <span>识别模型</span>
          <select
            value={value.asrModel}
            disabled={disabled}
            onChange={(event) => patch('asrModel', event.target.value as AsrModel)}
          >
            <option value="small">small · CPU 轻量</option>
            <option value="medium">medium · CPU 均衡</option>
            <option value="large-v3">large-v3 · 精度优先</option>
            <option value="large-v3-turbo">large-v3-turbo · 速度优先</option>
          </select>
        </label>
        <label className={`switch-row ${!cudaAvailable ? 'is-unavailable' : ''}`}>
          <span>
            <strong>启用 CUDA 加速</strong>
            <small>{cudaAvailable ? '使用 NVIDIA GPU 与 FP16' : '当前未检测到可用 CUDA'}</small>
          </span>
          <input
            type="checkbox"
            checked={value.useCuda && Boolean(cudaAvailable)}
            disabled={disabled || !cudaAvailable}
            onChange={(event) => patch('useCuda', event.target.checked)}
          />
          <i aria-hidden="true" />
        </label>
      </section>

      <section className="settings-section">
        <div className="section-heading">
          <h2>翻译设置</h2>
        </div>
        <label className="field target-language-field">
          <span>目标语言</span>
          <select
            value={value.targetLanguage}
            disabled={disabled}
            onChange={(event) => patch('targetLanguage', event.target.value as TargetLanguage)}
          >
            {TARGET_LANGUAGES.map((language) => (
              <option key={language.value} value={language.value}>{language.label}</option>
            ))}
          </select>
          <small>只生成一个双语字幕文件：源文在上，译文在下。</small>
        </label>
        <fieldset className="provider-fieldset" disabled={disabled}>
          <legend>翻译服务</legend>
          <div className="provider-tabs">
            {PROVIDERS.map((provider) => (
              <label key={provider.value} className={value.provider === provider.value ? 'is-selected' : ''}>
                <input
                  type="radio"
                  name="provider"
                  value={provider.value}
                  checked={value.provider === provider.value}
                  onChange={() => patch('provider', provider.value)}
                />
                {provider.label}
              </label>
            ))}
          </div>
        </fieldset>

        {value.provider === 'codex_spark' ? (
          <div className="provider-note">
            <ShieldCheck size={18} aria-hidden="true" />
            <div>
              <strong>使用本机 Codex CLI 登录</strong>
              <span>模型固定为 gpt-5.3-codex-spark，无需填写 API Key。</span>
            </div>
          </div>
        ) : null}

        {value.provider === 'lmstudio' ? (
          <>
            <label className="field">
              <span>模型 ID</span>
              <input
                value={value.lmstudioModel}
                disabled={disabled}
                onChange={(event) => patch('lmstudioModel', event.target.value)}
                placeholder="例如 qwen3-30b-a3b"
              />
            </label>
            <label className="field">
              <span>API Endpoint</span>
              <input
                value={value.lmstudioEndpoint}
                disabled={disabled}
                onChange={(event) => patch('lmstudioEndpoint', event.target.value)}
                spellCheck={false}
              />
            </label>
          </>
        ) : null}

        {value.provider === 'deepseek' ? (
          <>
            <label className="field">
              <span>模型</span>
              <input
                value={value.deepseekModel}
                disabled={disabled}
                onChange={(event) => patch('deepseekModel', event.target.value)}
              />
            </label>
            <label className="field">
              <span>API Endpoint</span>
              <input
                value={value.deepseekEndpoint}
                disabled={disabled}
                onChange={(event) => patch('deepseekEndpoint', event.target.value)}
                spellCheck={false}
              />
            </label>
            <label className="field">
              <span>API Key</span>
              <span className="password-field">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={value.deepseekApiKey}
                  disabled={disabled}
                  autoComplete="off"
                  onChange={(event) => patch('deepseekApiKey', event.target.value)}
                  placeholder="sk-…"
                />
                <button
                  type="button"
                  onClick={() => setShowKey((shown) => !shown)}
                  aria-label={showKey ? '隐藏 API Key' : '显示 API Key'}
                  disabled={disabled}
                >
                  {showKey ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </span>
              <small>密钥只随本次任务发送，后端不会在任务详情中回显。</small>
            </label>
          </>
        ) : null}
      </section>

      {children}

      <div className="start-area">
        <button
          type="button"
          className="start-button"
          onClick={onStart}
          disabled={!canStart || disabled}
        >
          <Play size={19} fill="currentColor" aria-hidden="true" />
          {disabled ? '正在生成字幕…' : '开始生成字幕'}
        </button>
        <p>{canStart ? '音视频留在本机；在线翻译仅发送字幕文本' : startHint}</p>
      </div>
    </aside>
  )
}
