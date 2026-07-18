import { AlertCircle, LoaderCircle, MousePointerClick } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  deleteJob,
  pickVideo,
  runBulkJobAction,
  runJobStep,
  updateJobStepConfig,
} from './api/client'
import { AboutPanel } from './components/AboutPanel'
import { AppHeader } from './components/AppHeader'
import { AppSidebar, type AppView } from './components/AppSidebar'
import { BatchCreator } from './components/BatchCreator'
import { CreateTaskDialog } from './components/CreateTaskDialog'
import { EnvironmentPanel } from './components/EnvironmentPanel'
import { JobListPanel } from './components/JobListPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { TaskConsole } from './components/TaskConsole'
import { TaskInspectorHeader } from './components/TaskInspectorHeader'
import { WorkflowPipeline } from './components/WorkflowPipeline'
import { useAppVersion } from './hooks/useAppVersion'
import { useBackendStatus } from './hooks/useBackendStatus'
import { useEnvironmentStatus } from './hooks/useEnvironmentStatus'
import { useJobSummaries } from './hooks/useJobSummaries'
import { useModelCatalog } from './hooks/useModelCatalog'
import { usePersistedSettings } from './hooks/usePersistedSettings'
import { useSelectedJob } from './hooks/useSelectedJob'
import { fileNameFromPath } from './lib/format'
import { validateHotwordText } from './lib/hotwords'
import type {
  AsrProvider,
  BatchConfigSnapshot,
  BatchCreateResult,
  JobBulkAction,
  JobStepConfig,
  JobStepId,
  MediaStepConfig,
  TranslationStepConfig,
} from './types/api'

const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function App() {
  const {
    connected,
    checking: backendChecking,
    health: backendHealth,
    capabilities,
    error: backendError,
    refresh: refreshBackend,
  } = useBackendStatus()
  const desktopVersion = useAppVersion()
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
  const [settings, setSettings] = usePersistedSettings()
  const summaryStore = useJobSummaries(connected)
  const [activeView, setActiveView] = useState<AppView>('tasks')
  const [creatorOpen, setCreatorOpen] = useState(false)
  const [batchBusy, setBatchBusy] = useState(false)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [checkedJobIds, setCheckedJobIds] = useState<Set<string>>(new Set())
  const [mutatingJobIds, setMutatingJobIds] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [mutationNotice, setMutationNotice] = useState<{
    message: string
    tone: 'success' | 'warning' | 'error'
  } | null>(null)
  const [detailErrors, setDetailErrors] = useState<Record<string, string | null>>({})
  const [runtimeKeys, setRuntimeKeys] = useState<Record<string, string>>({})
  const selectedJob = useSelectedJob(selectedJobId, connected)
  const selectedJobDetail = selectedJob.job
  const refreshSelectedJob = selectedJob.refresh

  const selectedModel = models.find((item) => item.id === settings.asrModel)
  const selectedAsrProvider: AsrProvider = selectedModel?.provider ?? 'faster_whisper'
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
  const hotwordValidation = useMemo(
    () => validateHotwordText(settings.asrHotwordsText),
    [settings.asrHotwordsText],
  )

  const batchConfig = useMemo<BatchConfigSnapshot>(() => {
    const model = settings.provider === 'codex_spark'
      ? 'gpt-5.3-codex-spark'
      : settings.provider === 'lmstudio'
        ? settings.lmstudioModel.trim() || null
        : settings.deepseekModel.trim() || null
    const endpoint = settings.provider === 'codex_spark'
      ? null
      : settings.provider === 'lmstudio'
        ? settings.lmstudioEndpoint.trim() || null
        : settings.deepseekEndpoint.trim() || null
    return {
      target_language: settings.targetLanguage,
      asr: {
        provider: selectedAsrProvider,
        model: settings.asrModel,
        device: settings.useCuda && cudaAvailable ? 'cuda' : 'cpu',
        compute_type: settings.useCuda && cudaAvailable ? 'float16' : 'int8',
        vad_filter: settings.asrVadFilter,
        dynamic_chunking: settings.asrDynamicChunking,
        selective_retry: settings.asrSelectiveRetry,
        timestamp_normalization: settings.asrTimestampNormalization,
        beam_size: settings.asrBeamSize,
        output_mode: settings.asrOutputMode,
        hotwords: hotwordValidation.hotwords,
      },
      translation: {
        target_language: settings.targetLanguage,
        provider: settings.provider,
        model,
        endpoint,
        timeout_seconds: settings.translationTimeoutSeconds,
      },
      export: {
        output_directory: settings.exportOutputDirectory.trim() || null,
        overwrite_existing: settings.exportOverwriteExisting,
        format: 'srt',
        bilingual_order: 'source_then_translation',
      },
    }
  }, [
    cudaAvailable,
    hotwordValidation.hotwords,
    selectedAsrProvider,
    settings,
  ])

  const configValidationError = useMemo(() => {
    if (hotwordValidation.error) return hotwordValidation.error
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
    if (settings.provider === 'codex_spark' && codexStatus === 'not_installed') {
      return '请先安装 Codex 并刷新检测'
    }
    if (settings.provider === 'codex_spark' && codexStatus === 'not_logged_in') {
      return '请先完成 Codex 登录并刷新检测'
    }
    if (settings.provider === 'codex_spark' && codexStatus === 'check_failed') {
      return 'Codex 状态检测失败，请刷新重试'
    }
    if (settings.provider === 'lmstudio' && !settings.lmstudioModel.trim()) {
      return '请填写 LM Studio 模型 ID'
    }
    return null
  }, [
    codexStatus,
    environment,
    environmentChecking,
    environmentError,
    hotwordValidation.error,
    modelsChecking,
    modelsError,
    selectedAsrCapability,
    selectedModelStatus,
    settings.lmstudioModel,
    settings.provider,
  ])
  const startValidationError = configValidationError
    ?? (settings.provider === 'deepseek' && !settings.deepseekApiKey.trim()
      ? '创建并启动 DeepSeek 任务前，请填写本次运行的 API Key'
      : null)

  useEffect(() => {
    if (!summaryStore.items.length) {
      if (!summaryStore.loading) setSelectedJobId(null)
      return
    }
    if (!selectedJobId || !summaryStore.items.some((item) => item.id === selectedJobId)) {
      setSelectedJobId(summaryStore.items[0].id)
    }
  }, [selectedJobId, summaryStore.items, summaryStore.loading])

  const selectedSummary = useMemo(
    () => summaryStore.items.find((item) => item.id === selectedJobId) ?? null,
    [selectedJobId, summaryStore.items],
  )

  useEffect(() => {
    if (!selectedSummary || !selectedJobDetail) return
    if (selectedSummary.updated_at > selectedJobDetail.updated_at) refreshSelectedJob()
  }, [refreshSelectedJob, selectedJobDetail, selectedSummary])

  useEffect(() => {
    const existingIds = new Set(summaryStore.items.map((item) => item.id))
    setCheckedJobIds((current) => {
      const next = new Set([...current].filter((jobId) => existingIds.has(jobId)))
      return next.size === current.size ? current : next
    })
  }, [summaryStore.items])

  const setJobMutation = useCallback((jobId: string, busy: boolean) => {
    setMutatingJobIds((current) => {
      const next = new Set(current)
      if (busy) next.add(jobId)
      else next.delete(jobId)
      return next
    })
  }, [])

  const setDetailError = useCallback((jobId: string, message: string | null) => {
    setDetailErrors((current) => ({ ...current, [jobId]: message }))
  }, [])

  const handleBatchCreated = useCallback((
    result: BatchCreateResult,
    runtimeApiKey: string,
  ) => {
    const created = result.results.flatMap((item) => item.ok && item.job ? [item.job] : [])
    summaryStore.upsert(created)
    if (runtimeApiKey) {
      setRuntimeKeys((current) => {
        const next = { ...current }
        created.forEach((item) => { next[item.id] = runtimeApiKey })
        return next
      })
    }
    if (created[0]) setSelectedJobId(created[0].id)
    if (result.failed_count === 0 && result.created_count > 0) setCreatorOpen(false)
    summaryStore.refresh()
  }, [summaryStore])

  const handleBulkAction = useCallback(async (action: JobBulkAction) => {
    const jobIds = [...checkedJobIds]
    if (!jobIds.length) return
    setBulkBusy(true)
    setMutationNotice(null)
    try {
      const runtimeApiKey = action === 'run' || action === 'retry_failed'
        ? settings.deepseekApiKey.trim()
        : ''
      const response = await runBulkJobAction({
        action,
        job_ids: jobIds,
        api_key: runtimeApiKey || undefined,
        continue_pipeline: true,
      })
      const successful = response.results.filter((item) => item.ok)
      const failed = response.results.filter((item) => !item.ok)
      const requestedIds = new Set(jobIds)
      const successfulIds = new Set(successful.map((item) => item.job_id))
      summaryStore.upsert(successful.flatMap((item) => item.job ? [item.job] : []))
      if (action === 'delete') {
        summaryStore.remove([...successfulIds])
        setSelectedJobId((current) => current && successfulIds.has(current) ? null : current)
      }
      if (action === 'delete' && successful.length) {
        setRuntimeKeys((current) => Object.fromEntries(
          Object.entries(current).filter(([jobId]) => !successfulIds.has(jobId)),
        ))
        setDetailErrors((current) => Object.fromEntries(
          Object.entries(current).filter(([jobId]) => !successfulIds.has(jobId)),
        ))
      }
      if (runtimeApiKey) {
        setRuntimeKeys((current) => {
          const next = { ...current }
          successful.forEach((item) => { next[item.job_id] = runtimeApiKey })
          return next
        })
      }
      setCheckedJobIds((current) => {
        const next = new Set([...current].filter((jobId) => !requestedIds.has(jobId)))
        failed.forEach((item) => next.add(item.job_id))
        return next
      })
      setMutationNotice({
        message: `${response.succeeded} 项成功${response.failed ? `，${response.failed} 项失败：${failed.map((item) => item.error ?? item.job_id.slice(0, 8)).join('；')}` : ''}`,
        tone: response.failed ? 'warning' : 'success',
      })
      if (action !== 'delete'
        && selectedJobId
        && successfulIds.has(selectedJobId)) {
        selectedJob.refresh()
      }
      summaryStore.refresh()
    } catch (error) {
      setMutationNotice({
        message: error instanceof Error ? error.message : '批量操作失败',
        tone: 'error',
      })
    } finally {
      setBulkBusy(false)
    }
  }, [
    checkedJobIds,
    selectedJob,
    selectedJobId,
    settings.deepseekApiKey,
    summaryStore,
  ])

  const handleUpdateJobStep = useCallback(async (
    step: JobStepId,
    config: JobStepConfig,
  ) => {
    const job = selectedJob.job
    if (!job) throw new Error('当前没有可修改的任务')
    setJobMutation(job.id, true)
    setDetailError(job.id, null)
    try {
      const updated = await updateJobStepConfig(job.id, step, config)
      selectedJob.setJobIfSelected(job.id, updated)
      summaryStore.refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : '无法更新步骤配置'
      setDetailError(job.id, message)
      throw error
    } finally {
      setJobMutation(job.id, false)
    }
  }, [selectedJob, setDetailError, setJobMutation, summaryStore])

  const handleRunJobStep = useCallback(async (step: JobStepId) => {
    const job = selectedJob.job
    if (!job) throw new Error('当前没有可运行的任务')
    const translationStep = job.steps.find((item) => item.id === 'translation')
    const translationConfig = translationStep?.config as TranslationStepConfig | undefined
    const runtimeApiKey = runtimeKeys[job.id]?.trim() ?? ''
    if (translationConfig?.provider === 'deepseek' && !runtimeApiKey) {
      const error = new Error('请先在翻译步骤中填写本次运行的 DeepSeek API Key')
      setDetailError(job.id, error.message)
      throw error
    }
    setJobMutation(job.id, true)
    setDetailError(job.id, null)
    try {
      const started = await runJobStep(job.id, step, {
        api_key: runtimeApiKey || undefined,
        continue_pipeline: true,
      })
      selectedJob.setJobIfSelected(job.id, started)
      selectedJob.refresh()
      summaryStore.refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : '无法运行任务步骤'
      setDetailError(job.id, message)
      throw error
    } finally {
      setJobMutation(job.id, false)
    }
  }, [runtimeKeys, selectedJob, setDetailError, setJobMutation, summaryStore])

  const handleReplaceTaskMedia = useCallback(async () => {
    const job = selectedJob.job
    if (!job) throw new Error('当前没有可替换媒体的任务')
    setJobMutation(job.id, true)
    setDetailError(job.id, null)
    try {
      const picked = await pickVideo()
      if (!picked.path) return
      const config: MediaStepConfig = {
        source_kind: 'path',
        path: picked.path,
        name: picked.name ?? fileNameFromPath(picked.path),
      }
      const updated = await updateJobStepConfig(job.id, 'media', config)
      selectedJob.setJobIfSelected(job.id, updated)
      summaryStore.refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : '无法替换任务视频'
      setDetailError(job.id, message)
      throw error
    } finally {
      setJobMutation(job.id, false)
    }
  }, [selectedJob, setDetailError, setJobMutation, summaryStore])

  const handleDeleteJob = useCallback(async () => {
    const job = selectedJob.job
    if (!job) return
    setJobMutation(job.id, true)
    setDetailError(job.id, null)
    try {
      await deleteJob(job.id)
      summaryStore.remove([job.id])
      setSelectedJobId((current) => current === job.id ? null : current)
      setCheckedJobIds((current) => {
        if (!current.has(job.id)) return current
        const next = new Set(current)
        next.delete(job.id)
        return next
      })
      setRuntimeKeys((current) => {
        const next = { ...current }
        delete next[job.id]
        return next
      })
      setDetailErrors((current) => {
        const next = { ...current }
        delete next[job.id]
        return next
      })
      summaryStore.refresh()
    } catch (error) {
      setDetailError(job.id, error instanceof Error ? error.message : '无法删除任务')
    } finally {
      setJobMutation(job.id, false)
    }
  }, [selectedJob, setDetailError, setJobMutation, summaryStore])

  const handleEnvironmentRefresh = useCallback(async () => {
    await Promise.all([refreshEnvironment(), refreshModels()])
  }, [refreshEnvironment, refreshModels])

  const handleFullRefresh = useCallback(async () => {
    await Promise.all([refreshBackend(), refreshEnvironment(), refreshModels()])
    summaryStore.refresh()
  }, [refreshBackend, refreshEnvironment, refreshModels, summaryStore])

  const handleToggleJob = useCallback((jobId: string, checked: boolean) => {
    setCheckedJobIds((current) => {
      const next = new Set(current)
      if (checked) next.add(jobId)
      else next.delete(jobId)
      return next
    })
  }, [])

  const handleToggleVisible = useCallback((jobIds: string[], checked: boolean) => {
    setCheckedJobIds((current) => {
      const next = new Set(current)
      jobIds.forEach((jobId) => {
        if (checked) next.add(jobId)
        else next.delete(jobId)
      })
      return next
    })
  }, [])

  const detailJob = selectedJob.job
  const selectedActive = Boolean(detailJob && ACTIVE_STATUSES.has(detailJob.status))
  const selectedMutationBusy = Boolean(selectedJobId && mutatingJobIds.has(selectedJobId))
  const checkedMutationBusy = [...checkedJobIds].some((jobId) => mutatingJobIds.has(jobId))
  const detailApiKey = selectedJobId ? runtimeKeys[selectedJobId] ?? '' : ''
  const detailError = selectedJobId ? detailErrors[selectedJobId] ?? null : null

  return (
    <div className="app-shell">
      <AppHeader
        connected={connected}
        checking={backendChecking}
        cudaAvailable={cudaAvailable}
        onRefresh={() => void handleFullRefresh()}
      />
      <div className="app-layout">
        <AppSidebar activeView={activeView} onSelect={setActiveView} />
        <main className="app-workspace">
          {activeView === 'tasks' ? (
            <section className="master-detail-layout" aria-label="任务列表与详情">
              <JobListPanel
                items={summaryStore.items}
                batches={summaryStore.batches}
                selectedJobId={selectedJobId}
                checkedJobIds={checkedJobIds}
                loading={summaryStore.loading}
                error={summaryStore.error ?? backendError}
                mutationNotice={mutationNotice}
                bulkBusy={bulkBusy || checkedMutationBusy}
                onCreateTask={() => setCreatorOpen(true)}
                onRefresh={summaryStore.refresh}
                onSelectJob={setSelectedJobId}
                onToggleJob={handleToggleJob}
                onToggleVisible={handleToggleVisible}
                onBulkAction={(action) => void handleBulkAction(action)}
              />

              <section className="job-detail-panel" aria-label="当前任务详情">
                {selectedJob.loading && !detailJob ? (
                  <div className="job-detail-state" role="status">
                    <LoaderCircle size={24} className="is-spinning" />
                    <strong>正在加载任务详情</strong>
                  </div>
                ) : selectedJob.error && !detailJob ? (
                  <div className="job-detail-state is-error" role="alert">
                    <AlertCircle size={24} />
                    <strong>无法加载任务详情</strong>
                    <span>{selectedJob.error}</span>
                    <button type="button" className="button button-ghost" onClick={selectedJob.refresh}>重试</button>
                  </div>
                ) : detailJob ? (
                  <>
                    <TaskInspectorHeader job={detailJob} />
                    <WorkflowPipeline
                      key={detailJob.id}
                      job={detailJob}
                      disabled={selectedActive || selectedMutationBusy}
                      cudaAvailable={cudaAvailable}
                      apiKey={detailApiKey}
                      onApiKeyChange={(value) => {
                        setRuntimeKeys((current) => ({ ...current, [detailJob.id]: value }))
                      }}
                      onUpdateStep={handleUpdateJobStep}
                      onRunStep={handleRunJobStep}
                      onReplaceMedia={handleReplaceTaskMedia}
                    />
                    <TaskConsole
                      job={detailJob}
                      pollError={selectedJob.error}
                      actionError={detailError}
                      onActionError={(message) => setDetailError(detailJob.id, message)}
                      onDeleteJob={() => void handleDeleteJob()}
                      disabled={selectedMutationBusy}
                    />
                  </>
                ) : (
                  <div className="job-detail-state">
                    <MousePointerClick size={26} />
                    <strong>选择一个任务查看详情</strong>
                    <span>任务在后台继续执行，切换详情不会中断队列。</span>
                  </div>
                )}
              </section>
            </section>
          ) : activeView === 'services' ? (
            <section className="utility-workspace" aria-labelledby="services-page-title">
              <header className="utility-workspace-header">
                <div>
                  <h2 id="services-page-title">模型与服务</h2>
                  <p>检查本机运行环境、识别模型与翻译服务。</p>
                </div>
                <button type="button" className="button button-secondary" onClick={() => void handleEnvironmentRefresh()}>
                  刷新检测
                </button>
              </header>
              <div className="utility-workspace-content">
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
                  disabled={false}
                  onRefresh={() => void handleEnvironmentRefresh()}
                  onDownloadModel={(id) => void startDownload(id)}
                />
              </div>
            </section>
          ) : (
            <section className="utility-workspace" aria-labelledby="settings-page-title">
              <header className="utility-workspace-header">
                <div>
                  <h2 id="settings-page-title">设置</h2>
                  <p>管理新任务默认识别、翻译与导出配置。</p>
                </div>
              </header>
              <div className="utility-workspace-content settings-page-content">
                <SettingsPanel
                  value={settings}
                  cudaAvailable={cudaAvailable}
                  disabled={false}
                  canStart={!configValidationError}
                  startHint={configValidationError ?? '配置可用'}
                  showStartAction={false}
                  initiallyOpen
                  onChange={setSettings}
                  onStart={() => undefined}
                />
                <AboutPanel
                  desktopVersion={desktopVersion}
                  sidecarConnected={connected}
                  sidecarChecking={backendChecking}
                  sidecarVersion={backendHealth?.version ?? null}
                />
              </div>
            </section>
          )}
        </main>
      </div>
      <CreateTaskDialog
        open={creatorOpen}
        busy={batchBusy}
        onRequestClose={() => setCreatorOpen(false)}
      >
        <BatchCreator
          connected={connected}
          config={batchConfig}
          configError={configValidationError}
          startError={startValidationError}
          runtimeApiKey={settings.deepseekApiKey}
          onBusyChange={setBatchBusy}
          onCreated={handleBatchCreated}
          onClose={() => setCreatorOpen(false)}
        >
          <SettingsPanel
            value={settings}
            cudaAvailable={cudaAvailable}
            disabled={batchBusy}
            canStart={!configValidationError}
            startHint={configValidationError ?? '批次配置可用'}
            showStartAction={false}
            initiallyOpen
            onChange={setSettings}
            onStart={() => undefined}
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
              disabled={batchBusy}
              onRefresh={() => void handleEnvironmentRefresh()}
              onDownloadModel={(id) => void startDownload(id)}
            />
          </SettingsPanel>
        </BatchCreator>
      </CreateTaskDialog>
    </div>
  )
}
