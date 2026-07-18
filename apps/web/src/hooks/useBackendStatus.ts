import { useCallback, useEffect, useState } from 'react'

import { getCapabilities, getHealth } from '../api/client'
import type { BackendCapabilities, BackendHealth } from '../types/api'

interface BackendState {
  connected: boolean
  checking: boolean
  health: BackendHealth | null
  capabilities: BackendCapabilities
  error: string | null
}

const initialState: BackendState = {
  connected: false,
  checking: true,
  health: null,
  capabilities: {},
  error: null,
}

export function useBackendStatus() {
  const [state, setState] = useState<BackendState>(initialState)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setState((current) => ({ ...current, checking: true }))
    const [health, capabilities] = await Promise.allSettled([
      getHealth(signal),
      getCapabilities(signal),
    ])

    if (signal?.aborted) return
    if (health.status === 'rejected') {
      setState({
        connected: false,
        checking: false,
        health: null,
        capabilities: capabilities.status === 'fulfilled' ? capabilities.value : {},
        error: health.reason instanceof Error ? health.reason.message : '无法连接本地服务',
      })
      return
    }

    setState({
      connected: true,
      checking: false,
      health: health.value,
      capabilities: capabilities.status === 'fulfilled' ? capabilities.value : {},
      error: capabilities.status === 'rejected' ? '能力信息暂不可用' : null,
    })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void refresh(controller.signal)
    return () => controller.abort()
  }, [refresh])

  return { ...state, refresh }
}
