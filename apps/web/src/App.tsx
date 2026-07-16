import { useCallback, useMemo, useState } from 'react'

import { createJob, pickVideo } from './api/client'
import { AppHeader } from './components/AppHeader'
import { EnvironmentPanel } from './components/EnvironmentPanel'
import { HeroIntro } from './components/HeroIntro'
import { SettingsPanel, type SettingsValue } from './components/SettingsPanel'
import { SourcePicker, type SelectedSource } from './components/SourcePicker'
import { TaskConsole } from './components/TaskConsole'
import { WorkflowProgress } from './components/WorkflowProgress'
import { useBackendStatus } from './hooks/useBackendStatus'
import { useEnvironmentStatus } from './hooks/useEnvironmentStatus'
import { useJobPolling } from './hooks/useJobPolling'
import { useModelCatalog } from './hooks/useModelCatalog'
import { fileNameFromPath } from './lib/format'
import type { AsrProvider, JobRequest, JobView } from './types/api'

const initialSettings: SettingsValue = {
  targetLanguage: 'zh-CN',
  asrModel: 'small',
  asrOutputMode: 'word_resegmented',
  useCuda: true,
  provider: 'codex_spark',
  lmstudioEndpoint: 'http://127.0.0.1:1234/v1',
  lmstudioModel: '',
  deepseekEndpoint: 'https://api.deepseek.com',
  deepseekModel: 'deepseek-v4-flash',
  deepseekApiKey: '',
}

const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function App() {
  const {
    connected,
    checking: backendChecking,
    capabilities,
    error: backendError,
    refresh: refreshBackend,
  } = useBackendStatus()
  const {
    data: environment,
    checking: environmentChecking,
    error: environmentError,
    refresh: refreshEnvironment,
  } = useEnvironmentStatus()
  const {
    items: models,
    model_root: modelRoot,
    checking: modelsChecking,
    downloadingId,
    error: modelsError,
    refresh: refreshModels,
    startDownload,
  } = useModelCatalog()
  const [source, setSource] = useState<SelectedSource | null>(null)
  const [settings, setSettings] = useState(initialSettings)
  const [initialJob, setInitialJob] = useState<JobView | null>(null)
  const [sourceBusy, setSourceBusy] = useState(false)
  const [startBusy, setStartBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const { job, pollError } = useJobPolling(initialJob)
  const taskActive = Boolean(job && ACTIVE_STATUSES.has(job.status)) || startBusy
  const selectedModel = models.find((item) => item.id === settings.asrModel)
  const selectedAsrProvider: AsrProvider = selectedModel?.provider
    ?? 'faster_whisper'
  const selectedAsrCapability = capabilities.asr?.providers?.find(
    (provider) => provider.id === selectedAsrProvider,
  )
  const cudaAvailable = selectedAsrCapability?.cuda_available
    ?? environment?.acceleration.cuda_available
    ?? Boolean(capabilities.asr?.cuda_available)
  const environmentModelMatches = environment?.model.name === settings.asrModel
  const selectedModelStatus = selectedModel?.status
    ?? (environmentModelMatches ? environment?.model.status : undefined)
  const codexStatus = environment?.codex.status

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
    if (environmentChecking || modelsChecking) return '正在检测运行环境'
    if (environmentError) return '运行环境检测失败，请刷新检测'
    if (selectedAsrCapability && !selectedAsrCapability.installed) {
      return `${selectedAsrCapability.label} 运行时尚未安装`
    }
    if (!selectedAsrCapability && environment?.asr.status !== 'ready') {
      return environment?.asr.message ?? '语音识别组件不可用'
    }
    if (environment?.tools.media.status !== 'ready') {
      return environment?.tools.media.message ?? '媒体解码组件不可用'
    }
    if (!selectedModelStatus) return modelsError ?? '无法获取识别模型状态，请刷新检测'
    if (selectedModelStatus === 'missing') return '请先下载识别模型'
    if (selectedModelStatus === 'damaged') return '识别模型已损坏，请重新下载'
    if (selectedModelStatus === 'downloading') return '识别模型正在下载，进度会自动更新'
    if (settings.provider === 'codex_spark' && codexStatus === 'not_installed') return '请先安装 Codex 并刷新检测'
    if (settings.provider === 'codex_spark' && codexStatus === 'not_logged_in') return '请先完成 Codex 登录并刷新检测'
    if (settings.provider === 'codex_spark' && codexStatus === 'check_failed') return 'Codex 状态检测失败，请刷新重试'
    if (settings.provider === 'lmstudio' && !settings.lmstudioModel.trim()) return '请填写 LM Studio 模型 ID'
    if (settings.provider === 'deepseek' && !settings.deepseekApiKey.trim()) return '请填写 DeepSeek API Key'
    return null
  }, [
    codexStatus,
    environment,
    environmentChecking,
    environmentError,
    modelsChecking,
    modelsError,
    selectedModelStatus,
    selectedAsrCapability,
    settings.deepseekApiKey,
    settings.lmstudioModel,
    settings.provider,
    source,
  ])

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
      video_path: source.path,
      target_language: settings.targetLanguage,
      asr: {
        provider: selectedAsrProvider,
        model: settings.asrModel,
        device: settings.useCuda && cudaAvailable ? 'cuda' : 'cpu',
        compute_type: settings.useCuda && cudaAvailable ? 'float16' : 'int8',
        vad_filter: true,
        beam_size: 5,
        output_mode: settings.asrOutputMode,
      },
      translation,
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
  }, [cudaAvailable, selectedAsrProvider, settings, source, validationError])

  const canStart = connected && Boolean(source) && !sourceBusy && !validationError
  const startHint = sourceBusy
    ? '正在选择视频，请稍候'
    : !connected
      ? '请先检查本地服务'
      : validationError ?? '请检查任务设置'

  const handleEnvironmentRefresh = useCallback(async () => {
    await Promise.all([refreshEnvironment(), refreshModels()])
  }, [refreshEnvironment, refreshModels])

  const handleFullRefresh = useCallback(async () => {
    await Promise.all([refreshBackend(), refreshEnvironment(), refreshModels()])
  }, [refreshBackend, refreshEnvironment, refreshModels])

  const handleDownloadModel = useCallback((id: string) => {
    void startDownload(id)
  }, [startDownload])

  return (
    <div className="app-shell">
      <AppHeader
        connected={connected}
        checking={backendChecking}
        cudaAvailable={cudaAvailable}
        onRefresh={() => void handleFullRefresh()}
      />
      <HeroIntro />
      <main className="task-workbench">
        <div className="task-primary-column">
          <SourcePicker
            source={source}
            busy={sourceBusy}
            disabled={taskActive}
            onPickPath={handlePickPath}
            onClear={() => {
              setSource(null)
              setInitialJob(null)
              setActionError(null)
            }}
          />
          <TaskConsole
            job={job}
            pollError={pollError}
            actionError={actionError ?? backendError}
            onActionError={setActionError}
          />
        </div>
        <SettingsPanel
          value={settings}
          cudaAvailable={cudaAvailable}
          disabled={taskActive}
          canStart={canStart}
          startHint={startHint}
          onChange={setSettings}
          onStart={() => void handleStart()}
          progress={<WorkflowProgress job={job} />}
        >
          <EnvironmentPanel
            environment={environment}
            checking={environmentChecking}
            error={environmentError}
            selectedModel={settings.asrModel}
            models={models}
            modelRoot={modelRoot}
            modelsChecking={modelsChecking}
            modelsError={modelsError}
            downloadingModelId={downloadingId}
            disabled={taskActive}
            onRefresh={() => void handleEnvironmentRefresh()}
            onDownloadModel={handleDownloadModel}
          />
        </SettingsPanel>
      </main>
    </div>
  )
}
