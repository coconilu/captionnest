import { useCallback, useEffect, useState } from 'react'

import { downloadModel, getModels } from '../api/client'
import type { ModelCatalog, ModelItem } from '../types/api'

interface ModelCatalogState extends ModelCatalog {
  checking: boolean
  downloadingId: string | null
  error: string | null
}

const initialState: ModelCatalogState = {
  items: [],
  model_root: '',
  checking: true,
  downloadingId: null,
  error: null,
}

function replaceModel(items: ModelItem[], next: ModelItem) {
  const index = items.findIndex((item) => item.id === next.id)
  if (index < 0) return [...items, next]
  return items.map((item) => (item.id === next.id ? next : item))
}

export function useModelCatalog() {
  const [state, setState] = useState<ModelCatalogState>(initialState)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setState((current) => ({ ...current, checking: true, error: null }))
    try {
      const catalog = await getModels(signal)
      if (signal?.aborted) return
      setState((current) => ({
        ...current,
        ...catalog,
        checking: false,
        error: null,
      }))
    } catch (error) {
      if (signal?.aborted) return
      setState((current) => ({
        ...current,
        checking: false,
        error: error instanceof Error ? error.message : '无法获取模型状态',
      }))
    }
  }, [])

  const startDownload = useCallback(async (id: string) => {
    setState((current) => ({ ...current, downloadingId: id, error: null }))
    try {
      const item = await downloadModel(id)
      setState((current) => ({
        ...current,
        items: replaceModel(current.items, item),
        downloadingId: null,
      }))
    } catch (error) {
      setState((current) => ({
        ...current,
        downloadingId: null,
        error: error instanceof Error ? error.message : '无法启动模型下载',
      }))
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void refresh(controller.signal)
    return () => controller.abort()
  }, [refresh])

  return { ...state, refresh, startDownload }
}
