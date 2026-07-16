import { useCallback, useEffect, useRef, useState } from 'react'

import { getJob } from '../api/client'
import type { JobView } from '../types/api'

const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function useSelectedJob(jobId: string | null, connected: boolean) {
  const [job, setJob] = useState<JobView | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)
  const selectedIdRef = useRef(jobId)
  selectedIdRef.current = jobId

  const refresh = useCallback(() => setRefreshToken((value) => value + 1), [])
  const setJobIfSelected = useCallback((expectedJobId: string, next: JobView) => {
    if (selectedIdRef.current === expectedJobId) setJob(next)
  }, [])

  useEffect(() => {
    if (!jobId || !connected) {
      setJob(null)
      setLoading(false)
      setError(null)
      return
    }

    setJob((current) => current?.id === jobId ? current : null)
    setError(null)
    let active = true
    let timeoutId: number | undefined
    const controller = new AbortController()

    const load = async (initial: boolean) => {
      if (initial) setLoading(true)
      try {
        const next = await getJob(jobId, controller.signal)
        if (!active) return
        setJob(next)
        setError(null)
        if (ACTIVE_STATUSES.has(next.status)) {
          timeoutId = window.setTimeout(() => void load(false), 900)
        }
      } catch (loadError) {
        if (!active || controller.signal.aborted) return
        setError(loadError instanceof Error ? loadError.message : '无法获取任务详情')
        timeoutId = window.setTimeout(() => void load(false), 2400)
      } finally {
        if (active && initial) setLoading(false)
      }
    }

    void load(true)
    return () => {
      active = false
      controller.abort()
      if (timeoutId) window.clearTimeout(timeoutId)
    }
  }, [connected, jobId, refreshToken])

  return {
    job,
    loading,
    error,
    refresh,
    setJobIfSelected,
  }
}
