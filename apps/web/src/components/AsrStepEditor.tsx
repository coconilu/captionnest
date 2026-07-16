import { useState } from 'react'

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
  const useCuda = draft.device === 'cuda' && cudaAvailable

  const selectModel = (model: AsrModel) => {
    const qwen = model === 'qwen3-asr-1.7b'
    setDraft((current) => ({
      ...current,
      model,
      provider: qwen ? 'qwen3_asr' : 'faster_whisper',
      output_mode: qwen ? 'word_resegmented' : current.output_mode,
    }))
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
          {draft.model === 'qwen3-asr-1.7b' ? (
            <option value="qwen3-asr-1.7b" disabled>Qwen3-ASR-1.7B · 兼容任务</option>
          ) : null}
        </select>
      </label>
      <label className="field">
        <span>字幕切分</span>
        <select
          value={draft.output_mode}
          disabled={saving || draft.provider === 'qwen3_asr'}
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
          disabled={saving || !cudaAvailable}
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
          disabled={saving}
          onChange={(event) => setDraft((current) => ({
            ...current,
            vad_filter: event.target.checked,
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
          disabled={saving}
          onChange={(event) => setDraft((current) => ({
            ...current,
            beam_size: Math.max(1, Math.min(20, Number(event.target.value) || 1)),
          }))}
        />
      </label>
      <div className="pipeline-editor-actions">
        <button type="button" onClick={onCancel} disabled={saving}>取消</button>
        <button type="submit" className="button-primary" disabled={saving}>
          {saving ? '保存中…' : '保存识别配置'}
        </button>
      </div>
    </form>
  )
}
