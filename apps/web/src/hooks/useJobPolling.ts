import { useEffect, useState } from 'react'

import { getJob } from '../api/client'
import type { JobView } from '../types/api'

const TERMINAL_STATUSES = new Set([
  'draft',
  'waiting_for_input',
  'completed',
  'failed',
  'cancelled',
  'interrupted',
])

export function useJobPolling(initialJob: JobView | null) {
  const [job, setJob] = useState<JobView | null>(initialJob)
  const [pollError, setPollError] = useState<string | null>(null)

  useEffect(() => setJob(initialJob), [initialJob])

  useEffect(() => {
    if (!job?.id || TERMINAL_STATUSES.has(job.status)) return

    let active = true
    let timeoutId: number | undefined
    const controller = new AbortController()

    const poll = async () => {
      try {
        const next = await getJob(job.id, controller.signal)
        if (!active) return
        setJob(next)
        setPollError(null)
        if (!TERMINAL_STATUSES.has(next.status)) timeoutId = window.setTimeout(poll, 1200)
      } catch (error) {
        if (!active || controller.signal.aborted) return
        setPollError(error instanceof Error ? error.message : '无法获取任务进度')
        timeoutId = window.setTimeout(poll, 2500)
      }
    }

    timeoutId = window.setTimeout(poll, 700)
    return () => {
      active = false
      controller.abort()
      if (timeoutId) window.clearTimeout(timeoutId)
    }
  }, [job?.id, job?.status])

  return { job, pollError }
}
