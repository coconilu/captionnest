import { AlertCircle } from 'lucide-react'
import { useState } from 'react'

import {
  HOTWORD_MAX_ENTRIES,
  HOTWORD_MAX_ENTRY_CHARACTERS,
  HOTWORD_MAX_TOTAL_CHARACTERS,
  validateHotwordText,
} from '../lib/hotwords'
import type { AsrModel, AsrStepConfig } from '../types/api'

interface AsrStepEditorProps {
  value: AsrStepConfig
  cudaAvailable: boolean
  saving: boolean
  onCancel: () => void
  onSave: (value: AsrStepConfig) => void
}

export function AsrStepEditor({
  value,
  cudaAvailable,
  saving,
  onCancel,
  onSave,
}: AsrStepEditorProps) {
  const [draft, setDraft] = useState(value)
  const [hotwordText, setHotwordText] = useState((value.hotwords ?? []).join('\n'))
  const hotwordValidation = validateHotwordText(hotwordText)
  const legacy = draft.provider === 'qwen3_asr'
  const useCuda = draft.device === 'cuda' && cudaAvailable

  const selectModel = (model: AsrModel) => {
    setDraft((current) => ({
      ...current,
      model,
      provider: 'faster_whisper',
      device: current.device === 'cuda' && cudaAvailable ? 'cuda' : 'cpu',
      dynamic_chunking: current.dynamic_chunking ?? false,
      selective_retry: current.selective_retry ?? false,
      compute_type:
        current.device === 'cuda' && cudaAvailable ? current.compute_type : 'int8',
    }))
  }

  return (
    <form
      className="pipeline-editor-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (hotwordValidation.error) return
        onSave({ ...draft, hotwords: hotwordValidation.hotwords })
      }}
    >
      <label className="field">
        <span>识别模型</span>
        <select
          value={draft.model}
          disabled={saving}
          onChange={(event) => selectModel(event.target.value as AsrModel)}
        >
          <option value="small">small · CPU 轻量</option>
          <option value="medium">medium · CPU 均衡</option>
          <option value="large-v3-turbo">large-v3-turbo · 速度优先</option>
          <option value="large-v3">large-v3 · 精度优先</option>
          {legacy ? (
            <option value="qwen3-asr-1.7b" disabled>Qwen3-ASR-1.7B · 兼容任务</option>
          ) : null}
        </select>
      </label>
      {legacy ? (
        <div className="pipeline-step-error" role="alert">
          <AlertCircle size={15} />
          <span>Qwen3-ASR 已停用。请选择上方任一 Faster-Whisper 模型后保存。</span>
        </div>
      ) : null}
      <label className="field">
        <span>字幕切分</span>
        <select
          value={draft.output_mode}
          disabled={saving || legacy}
          onChange={(event) => setDraft((current) => ({
            ...current,
            output_mode: event.target.value as AsrStepConfig['output_mode'],
          }))}
        >
          <option value="word_resegmented">逐词重排</option>
          <option value="chunk_segments">分片原始段</option>
        </select>
      </label>
      <label className="switch-row">
        <span>
          <strong>CUDA 加速</strong>
          <small>{cudaAvailable ? '使用 NVIDIA GPU' : '当前不可用'}</small>
        </span>
        <input
          type="checkbox"
          checked={useCuda}
          disabled={saving || legacy || !cudaAvailable}
          onChange={(event) => setDraft((current) => ({
            ...current,
            device: event.target.checked ? 'cuda' : 'cpu',
            compute_type: event.target.checked ? 'float16' : 'int8',
          }))}
        />
        <i aria-hidden="true" />
      </label>
      <label className="switch-row">
        <span>
          <strong>语音活动检测</strong>
          <small>跳过长静音区域</small>
        </span>
        <input
          type="checkbox"
          checked={draft.vad_filter}
          disabled={saving || legacy}
          onChange={(event) => setDraft((current) => ({
            ...current,
            vad_filter: event.target.checked,
          }))}
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
          checked={draft.dynamic_chunking ?? false}
          disabled={saving || legacy}
          onChange={(event) => setDraft((current) => ({
            ...current,
            dynamic_chunking: event.target.checked,
          }))}
        />
        <i aria-hidden="true" />
      </label>
      <label className="switch-row">
        <span>
          <strong>低置信片段二次识别</strong>
          <small>只对可疑片段执行一次有界重识别</small>
        </span>
        <input
          type="checkbox"
          checked={draft.selective_retry ?? false}
          disabled={saving || legacy}
          onChange={(event) => setDraft((current) => ({
            ...current,
            selective_retry: event.target.checked,
          }))}
        />
        <i aria-hidden="true" />
      </label>
      <label className="field compact-number-field">
        <span>Beam Size</span>
        <input
          type="number"
          min={1}
          max={20}
          value={draft.beam_size}
          disabled={saving || legacy}
          onChange={(event) => setDraft((current) => ({
            ...current,
            beam_size: Math.max(1, Math.min(20, Number(event.target.value) || 1)),
          }))}
        />
      </label>
      <label className={`field hotwords-field ${hotwordValidation.error ? 'is-invalid' : ''}`}>
        <span>专有词 / Hotwords</span>
        <textarea
          rows={5}
          value={hotwordText}
          disabled={saving || legacy}
          aria-invalid={Boolean(hotwordValidation.error)}
          aria-describedby="task-hotwords-hint"
          onChange={(event) => setHotwordText(event.target.value)}
          placeholder={'每行一个词，例如：\nCaptionNest\n初音未来'}
        />
        <small
          id="task-hotwords-hint"
          className={hotwordValidation.error ? 'field-error-text' : undefined}
        >
          {hotwordValidation.error
            ?? `${hotwordValidation.hotwords.length}/${HOTWORD_MAX_ENTRIES} 条 · ${hotwordValidation.characterCount}/${HOTWORD_MAX_TOTAL_CHARACTERS} 字符 · 单条最多 ${HOTWORD_MAX_ENTRY_CHARACTERS} 字符`}
        </small>
      </label>
      <div className="pipeline-editor-actions">
        <button type="button" onClick={onCancel} disabled={saving}>取消</button>
        <button
          type="submit"
          className="button-primary"
          disabled={saving || legacy || Boolean(hotwordValidation.error)}
        >
          {saving ? '保存中…' : '保存识别配置'}
        </button>
      </div>
    </form>
  )
}
