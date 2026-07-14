import { useCallback, useEffect, useState } from 'react'

import { getEnvironment } from '../api/client'
import type { EnvironmentView } from '../types/api'

interface EnvironmentState {
  data: EnvironmentView | null
  checking: boolean
  error: string | null
}

const initialState: EnvironmentState = {
  data: null,
  checking: true,
  error: null,
}

export function useEnvironmentStatus() {
  const [state, setState] = useState<EnvironmentState>(initialState)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setState((current) => ({ ...current, checking: true, error: null }))
    try {
      const data = await getEnvironment(signal)
      if (signal?.aborted) return
      setState({ data, checking: false, error: null })
    } catch (error) {
      if (signal?.aborted) return
      setState((current) => ({
        ...current,
        checking: false,
        error: error instanceof Error ? error.message : '无法检测运行环境',
      }))
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void refresh(controller.signal)
    return () => controller.abort()
  }, [refresh])

  return { ...state, refresh }
}
