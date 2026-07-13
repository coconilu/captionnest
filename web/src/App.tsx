import { useCallback, useEffect, useMemo, useState } from 'react'

import { createJob, pickVideo, uploadVideo } from './api/client'
import { AppHeader } from './components/AppHeader'
import { SettingsPanel, type SettingsValue } from './components/SettingsPanel'
import { SourcePicker, type SelectedSource } from './components/SourcePicker'
import { TaskConsole } from './components/TaskConsole'
import { WorkflowProgress } from './components/WorkflowProgress'
import { useBackendStatus } from './hooks/useBackendStatus'
import { useJobPolling } from './hooks/useJobPolling'
import { fileNameFromPath } from './lib/format'
import type { JobRequest, JobView } from './types/api'

const initialSettings: SettingsValue = {
  sourceLanguage: 'auto',
  asrModel: 'large-v3',
  useCuda: true,
  provider: 'codex_spark',
  lmstudioEndpoint: 'http://127.0.0.1:1234/v1',
  lmstudioModel: '',
  deepseekEndpoint: 'https://api.deepseek.com',
  deepseekModel: 'deepseek-v4-flash',
  deepseekApiKey: '',
  writeSourceSrt: true,
}

const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function App() {
  const backend = useBackendStatus()
  const [source, setSource] = useState<SelectedSource | null>(null)
  const [settings, setSettings] = useState(initialSettings)
  const [initialJob, setInitialJob] = useState<JobView | null>(null)
  const [sourceBusy, setSourceBusy] = useState(false)
  const [startBusy, setStartBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const { job, pollError } = useJobPolling(initialJob)
  const taskActive = Boolean(job && ACTIVE_STATUSES.has(job.status)) || startBusy
  const cudaAvailable = Boolean(backend.capabilities.asr?.cuda_available)

  useEffect(() => {
    if (!cudaAvailable && !backend.checking && settings.useCuda) {
      setSettings((current) => ({ ...current, useCuda: false }))
    }
  }, [backend.checking, cudaAvailable, settings.useCuda])

  const handleUpload = useCallback(async (file: File) => {
    setSourceBusy(true)
    setActionError(null)
    try {
      const uploaded = await uploadVideo(file)
      setSource({
        kind: 'upload',
        uploadId: uploaded.upload_id,
        name: uploaded.name,
        path: uploaded.path,
        size: uploaded.size,
      })
      setInitialJob(null)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '视频上传失败')
    } finally {
      setSourceBusy(false)
    }
  }, [])

  const handlePickPath = useCallback(async () => {
    setSourceBusy(true)
    setActionError(null)
    try {
      const picked = await pickVideo()
      if (!picked.path) return
      setSource({
        kind: 'path',
        path: picked.path,
        name: picked.name ?? fileNameFromPath(picked.path),
        size: picked.size ?? undefined,
      })
      setInitialJob(null)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法选择本机文件')
    } finally {
      setSourceBusy(false)
    }
  }, [])

  const validationError = useMemo(() => {
    if (!source) return '请选择视频'
    if (settings.provider === 'lmstudio' && !settings.lmstudioModel.trim()) return '请填写 LM Studio 模型 ID'
    if (settings.provider === 'deepseek' && !settings.deepseekApiKey.trim()) return '请填写 DeepSeek API Key'
    return null
  }, [settings.deepseekApiKey, settings.lmstudioModel, settings.provider, source])

  const handleStart = useCallback(async () => {
    if (!source || validationError) {
      setActionError(validationError)
      return
    }

    const translation: JobRequest['translation'] = { provider: settings.provider }
    if (settings.provider === 'codex_spark') translation.model = 'gpt-5.3-codex-spark'
    if (settings.provider === 'lmstudio') {
      translation.model = settings.lmstudioModel.trim()
      translation.endpoint = settings.lmstudioEndpoint.trim()
    }
    if (settings.provider === 'deepseek') {
      translation.model = settings.deepseekModel.trim()
      translation.endpoint = settings.deepseekEndpoint.trim()
      translation.api_key = settings.deepseekApiKey.trim()
    }

    const payload: JobRequest = {
      ...(source.kind === 'upload' ? { upload_id: source.uploadId } : { video_path: source.path }),
      source_language: settings.sourceLanguage,
      asr: {
        model: settings.asrModel,
        device: settings.useCuda && cudaAvailable ? 'cuda' : 'cpu',
        compute_type: settings.useCuda && cudaAvailable ? 'float16' : 'int8',
        vad_filter: true,
        beam_size: 5,
      },
      translation,
      output: { write_source_srt: settings.writeSourceSrt },
    }

    setStartBusy(true)
    setActionError(null)
    try {
      const created = await createJob(payload)
      setInitialJob(created)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '任务启动失败')
    } finally {
      setStartBusy(false)
    }
  }, [cudaAvailable, settings, source, validationError])

  const canStart = backend.connected && Boolean(source) && !sourceBusy && !validationError

  return (
    <div className="app-shell">
      <AppHeader
        connected={backend.connected}
        checking={backend.checking}
        cudaAvailable={cudaAvailable}
        onRefresh={() => void backend.refresh()}
      />
      <main className="app-layout">
        <div className="workspace">
          <SourcePicker
            source={source}
            busy={sourceBusy}
            disabled={taskActive}
            onUpload={handleUpload}
            onPickPath={handlePickPath}
            onClear={() => {
              setSource(null)
              setInitialJob(null)
              setActionError(null)
            }}
          />
          <WorkflowProgress job={job} />
          <TaskConsole
            job={job}
            pollError={pollError}
            actionError={actionError ?? backend.error}
            onActionError={setActionError}
          />
        </div>
        <SettingsPanel
          value={settings}
          cudaAvailable={cudaAvailable}
          disabled={taskActive}
          canStart={canStart}
          onChange={setSettings}
          onStart={() => void handleStart()}
        />
      </main>
    </div>
  )
}
