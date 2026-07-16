import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'

import type {
  TargetLanguage,
  TranslationProvider,
  TranslationStepConfig,
} from '../types/api'

interface TranslationStepEditorProps {
  value: TranslationStepConfig
  apiKey: string
  saving: boolean
  onApiKeyChange: (value: string) => void
  onCancel: () => void
  onSave: (value: TranslationStepConfig) => void
}
export function TranslationStepEditor({
  value,
  apiKey,
  saving,
  onApiKeyChange,
  onCancel,
  onSave,
}: TranslationStepEditorProps) {
  const [draft, setDraft] = useState(value)
  const [showKey, setShowKey] = useState(false)

  const selectProvider = (provider: TranslationProvider) => {
    setDraft((current) => {
      if (provider === 'codex_spark') {
        return {
          ...current,
          provider,
          model: 'gpt-5.3-codex-spark',
          endpoint: null,
        }
      }
      if (provider === 'lmstudio') {
        return {
          ...current,
          provider,
          model: current.provider === 'lmstudio' ? current.model : '',
          endpoint: current.provider === 'lmstudio'
            ? current.endpoint
            : 'http://127.0.0.1:1234/v1',
        }
      }
      return {
        ...current,
        provider,
        model: current.provider === 'deepseek' ? current.model : 'deepseek-v4-flash',
        endpoint: current.provider === 'deepseek'
          ? current.endpoint
          : 'https://api.deepseek.com',
      }
    })
  }

  return (
    <form
      className="pipeline-editor-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(draft)
      }}
    >
      <label className="field">
        <span>目标语言</span>
        <select
          value={draft.target_language}
          disabled={saving}
          onChange={(event) => setDraft((current) => ({
            ...current,
            target_language: event.target.value as TargetLanguage,
          }))}
        >
          <option value="zh-CN">简体中文</option>
          <option value="en">英语</option>
          <option value="ko">韩语</option>
        </select>
      </label>
      <fieldset className="provider-fieldset" disabled={saving}>
        <legend>翻译服务</legend>
        <div className="provider-tabs">
          {(['codex_spark', 'lmstudio', 'deepseek'] as const).map((provider) => (
            <label key={provider} className={draft.provider === provider ? 'is-selected' : ''}>
              <input
                type="radio"
                name="task-translation-provider"
                checked={draft.provider === provider}
                onChange={() => selectProvider(provider)}
              />
              {provider === 'codex_spark' ? 'Codex Spark' : provider === 'lmstudio' ? 'LM Studio' : 'DeepSeek'}
            </label>
          ))}
        </div>
      </fieldset>
      {draft.provider !== 'codex_spark' ? (
        <>
          <label className="field">
            <span>模型 ID</span>
            <input
              value={draft.model ?? ''}
              disabled={saving}
              required
              onChange={(event) => setDraft((current) => ({
                ...current,
                model: event.target.value,
              }))}
            />
          </label>
          <label className="field">
            <span>API Endpoint</span>
            <input
              value={draft.endpoint ?? ''}
              disabled={saving}
              required
              spellCheck={false}
              onChange={(event) => setDraft((current) => ({
                ...current,
                endpoint: event.target.value,
              }))}
            />
          </label>
        </>
      ) : null}
      {draft.provider === 'deepseek' ? (
        <label className="field">
          <span>本次运行 API Key</span>
          <span className="password-field">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              disabled={saving}
              autoComplete="off"
              onChange={(event) => onApiKeyChange(event.target.value)}
              placeholder="不会保存到任务或本地设置"
            />
            <button
              type="button"
              onClick={() => setShowKey((shown) => !shown)}
              aria-label={showKey ? '隐藏 API Key' : '显示 API Key'}
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </span>
        </label>
      ) : null}
      <label className="field compact-number-field">
        <span>请求超时（秒）</span>
        <input
          type="number"
          min={10}
          max={3600}
          value={draft.timeout_seconds}
          disabled={saving}
          onChange={(event) => setDraft((current) => ({
            ...current,
            timeout_seconds: Math.max(10, Math.min(3600, Number(event.target.value) || 10)),
          }))}
        />
      </label>
      <div className="pipeline-secret-note">API Key 仅进入下一次执行内存，不参与配置版本。</div>
      <div className="pipeline-editor-actions">
        <button type="button" onClick={onCancel} disabled={saving}>取消</button>
        <button type="submit" className="button-primary" disabled={saving}>
          {saving ? '保存中…' : '保存翻译配置'}
        </button>
      </div>
    </form>
  )
}
