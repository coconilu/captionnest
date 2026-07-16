import { Eye, EyeOff, Play, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import type { AsrModel, AsrOutputMode, TargetLanguage, TranslationProvider } from '../types/api'
import type { SettingsValue } from '../types/settings'

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

const MODEL_LABELS: Record<AsrModel, string> = {
  small: 'small',
  medium: 'medium',
  'large-v3': 'large-v3',
  'large-v3-turbo': 'large-v3-turbo',
}

const OUTPUT_MODES: Array<{
  value: AsrOutputMode
  label: string
  description: string
  recommended?: boolean
}> = [
  {
    value: 'word_resegmented',
    label: '逐词重排',
    description: '压缩词间静音，适合直接观看',
    recommended: true,
  },
  {
    value: 'chunk_segments',
    label: '分片原始段',
    description: '保留模型段落，适合诊断对照',
  },
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
  const [settingsOpen, setSettingsOpen] = useState(false)
  const patch = <K extends keyof SettingsValue>(key: K, next: SettingsValue[K]) =>
    onChange({ ...value, [key]: next })
  const targetLanguage = TARGET_LANGUAGES.find((item) => item.value === value.targetLanguage)?.label ?? value.targetLanguage
  const provider = PROVIDERS.find((item) => item.value === value.provider)?.label ?? value.provider
  const outputMode = OUTPUT_MODES.find((item) => item.value === value.asrOutputMode)?.label ?? value.asrOutputMode

  return (
    <aside className="settings-sidebar" aria-label="新任务默认设置">
      <section className="task-summary" aria-labelledby="task-summary-title">
        <span className="panel-step-label">默认配置</span>
        <div className="task-summary-heading">
          <div>
            <h2 id="task-summary-title">新任务默认配置</h2>
            <p>创建任务时复制，任务内修改不会反向覆盖</p>
          </div>
          <button
            type="button"
            className="summary-settings-button"
            onClick={() => setSettingsOpen((open) => !open)}
            aria-expanded={settingsOpen}
            aria-controls="task-settings-panel"
          >
            <SlidersHorizontal size={18} aria-hidden="true" />
            <span className="sr-only">{settingsOpen ? '收起任务设置' : '调整任务设置'}</span>
          </button>
        </div>
        <div className="summary-chips" aria-label="当前任务设置">
          <span>目标 · {targetLanguage}</span>
          <span>模型 · {MODEL_LABELS[value.asrModel]}</span>
          <span>切分 · {outputMode}</span>
          <span>边界 · {value.asrDynamicChunking ? '动态' : '固定'}</span>
          <span className="is-provider"><i aria-hidden="true" />{provider}</span>
        </div>
      </section>

      {settingsOpen ? (
        <div id="task-settings-panel" className="settings-drawer">
          <div className="settings-content">
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
            <fieldset className="output-mode-fieldset" disabled={disabled}>
              <legend>字幕切分</legend>
              <div className="output-mode-options">
                {OUTPUT_MODES.map((mode) => (
                  <label
                    key={mode.value}
                    className={value.asrOutputMode === mode.value ? 'is-selected' : ''}
                  >
                    <input
                      type="radio"
                      name="asr-output-mode"
                      value={mode.value}
                      checked={value.asrOutputMode === mode.value}
                      onChange={() => patch('asrOutputMode', mode.value)}
                    />
                    <span>
                      <strong>
                        {mode.label}
                        {mode.recommended ? <em>推荐</em> : null}
                      </strong>
                      <small>{mode.description}</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
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
            <label className="switch-row">
              <span>
                <strong>语音活动检测</strong>
                <small>跳过长静音区域，提高识别效率</small>
              </span>
              <input
                type="checkbox"
                checked={value.asrVadFilter}
                disabled={disabled}
                onChange={(event) => patch('asrVadFilter', event.target.checked)}
              />
              <i aria-hidden="true" />
            </label>
            <label className="switch-row">
              <span>
                <strong>动态切片边界</strong>
                <small>将 60 秒边界吸附到附近自然停顿</small>
              </span>
              <input
                type="checkbox"
                checked={value.asrDynamicChunking}
                disabled={disabled}
                onChange={(event) => patch('asrDynamicChunking', event.target.checked)}
              />
              <i aria-hidden="true" />
            </label>
            <label className="field compact-number-field">
              <span>Beam Size</span>
              <input
                type="number"
                min={1}
                max={20}
                value={value.asrBeamSize}
                disabled={disabled}
                onChange={(event) => patch(
                  'asrBeamSize',
                  Math.max(1, Math.min(20, Number(event.target.value) || 1)),
                )}
              />
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
                {PROVIDERS.map((providerOption) => (
                  <label key={providerOption.value} className={value.provider === providerOption.value ? 'is-selected' : ''}>
                    <input
                      type="radio"
                      name="provider"
                      value={providerOption.value}
                      checked={value.provider === providerOption.value}
                      onChange={() => patch('provider', providerOption.value)}
                    />
                    {providerOption.label}
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
                  <small>密钥只随本次运行发送，不会记住，也不会进入任务详情。</small>
                </label>
              </>
            ) : null}
            <label className="field compact-number-field">
              <span>请求超时（秒）</span>
              <input
                type="number"
                min={10}
                max={3600}
                value={value.translationTimeoutSeconds}
                disabled={disabled}
                onChange={(event) => patch(
                  'translationTimeoutSeconds',
                  Math.max(10, Math.min(3600, Number(event.target.value) || 10)),
                )}
              />
            </label>
          </section>

          <section className="settings-section">
            <div className="section-heading">
              <h2>导出设置</h2>
            </div>
            <label className="field">
              <span>默认输出目录</span>
              <input
                value={value.exportOutputDirectory}
                disabled={disabled}
                onChange={(event) => patch('exportOutputDirectory', event.target.value)}
                placeholder="留空则输出到源视频目录"
                spellCheck={false}
              />
            </label>
            <label className="switch-row">
              <span>
                <strong>覆盖同名字幕</strong>
                <small>保持 &lt;视频名&gt;.srt 的单文件输出规则</small>
              </span>
              <input
                type="checkbox"
                checked={value.exportOverwriteExisting}
                disabled={disabled}
                onChange={(event) => patch('exportOverwriteExisting', event.target.checked)}
              />
              <i aria-hidden="true" />
            </label>
          </section>

          {children}
          </div>
        </div>
      ) : null}

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
        <p>{canStart ? `输出：单个双语 SRT · ${outputMode}` : startHint}</p>
      </div>
    </aside>
  )
}
