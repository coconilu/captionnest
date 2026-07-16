import { useCallback, useEffect, useRef, useState } from 'react'

import { listBatches, listJobSummaries } from '../api/client'
import type { BatchRecord, JobSummaryView } from '../types/api'

const SUMMARY_PAGE_SIZE = 200
const ACTIVE_POLL_INTERVAL_MS = 1400
const HIDDEN_POLL_INTERVAL_MS = 4000
const BATCH_REFRESH_EVERY_ROUNDS = 4
const FULL_REFRESH_EVERY_ROUNDS = 40

interface SummaryRound {
  items: JobSummaryView[]
  serverTime: string
}

function sortSummaries(items: JobSummaryView[]): JobSummaryView[] {
  return [...items].sort((left, right) => {
    const created = right.created_at.localeCompare(left.created_at)
    return created || right.id.localeCompare(left.id)
  })
}

export function mergeJobSummaries(
  current: JobSummaryView[],
  incoming: JobSummaryView[],
): JobSummaryView[] {
  if (!incoming.length) return current
  const merged = new Map(current.map((item) => [item.id, item]))
  incoming.forEach((item) => merged.set(item.id, item))
  return sortSummaries([...merged.values()])
}

async function fetchSummaryRound(
  updatedAfter: string | null,
  signal: AbortSignal,
): Promise<SummaryRound> {
  const items: JobSummaryView[] = []
  let cursor: string | undefined
  let serverTime: string | null = null

  do {
    const page = await listJobSummaries(
      cursor
        ? { cursor, limit: SUMMARY_PAGE_SIZE }
        : {
            limit: SUMMARY_PAGE_SIZE,
            updatedAfter: updatedAfter ?? undefined,
          },
      signal,
    )
    if (serverTime !== null && page.server_time !== serverTime) {
      throw new Error('任务列表分页水位不一致，请重新加载')
    }
    serverTime = page.server_time
    items.push(...page.items)
    cursor = page.has_more && page.next_cursor ? page.next_cursor : undefined
  } while (cursor)

  if (!serverTime) throw new Error('任务列表响应缺少服务端水位')
  return { items, serverTime }
}

export function useJobSummaries(connected: boolean) {
  const [items, setItems] = useState<JobSummaryView[]>([])
  const [batches, setBatches] = useState<BatchRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)
  const watermarkRef = useRef<string | null>(null)

  const refresh = useCallback(() => {
    watermarkRef.current = null
    setRefreshToken((value) => value + 1)
  }, [])

  const upsert = useCallback((nextItems: JobSummaryView[]) => {
    setItems((current) => mergeJobSummaries(current, nextItems))
  }, [])

  const remove = useCallback((jobIds: string[]) => {
    if (!jobIds.length) return
    const removed = new Set(jobIds)
    setItems((current) => current.filter((item) => !removed.has(item.id)))
  }, [])

  useEffect(() => {
    if (!connected) {
      watermarkRef.current = null
      setLoading(false)
      return
    }

    let active = true
    let timeoutId: number | undefined
    let roundNumber = 0
    const controller = new AbortController()

    const schedule = () => {
      if (!active) return
      const delay = document.hidden ? HIDDEN_POLL_INTERVAL_MS : ACTIVE_POLL_INTERVAL_MS
      const full = roundNumber > 0 && roundNumber % FULL_REFRESH_EVERY_ROUNDS === 0
      timeoutId = window.setTimeout(() => void synchronize(full), delay)
    }

    const refreshBatchRecords = async () => {
      try {
        const nextBatches = await listBatches(controller.signal)
        if (active) setBatches(nextBatches)
      } catch (batchError) {
        if (!active || controller.signal.aborted) return
        setError(batchError instanceof Error ? batchError.message : '无法获取批次列表')
      }
    }

    const synchronize = async (full: boolean) => {
      const replaceSnapshot = full || watermarkRef.current === null
      if (replaceSnapshot) setLoading(true)
      try {
        const updatedAfter = replaceSnapshot ? null : watermarkRef.current
        const round = await fetchSummaryRound(updatedAfter, controller.signal)
        if (!active) return
        setItems((current) => replaceSnapshot
          ? sortSummaries(round.items)
          : mergeJobSummaries(current, round.items))
        watermarkRef.current = round.serverTime
        setError(null)
        roundNumber += 1
        if (replaceSnapshot || roundNumber % BATCH_REFRESH_EVERY_ROUNDS === 0) {
          await refreshBatchRecords()
        }
      } catch (syncError) {
        if (!active || controller.signal.aborted) return
        setError(syncError instanceof Error ? syncError.message : '无法刷新任务列表')
        // A process restart invalidates both the watermark context and any in-flight cursor.
        // The next synchronization therefore starts from a complete snapshot.
        watermarkRef.current = null
      } finally {
        if (active) {
          setLoading(false)
          schedule()
        }
      }
    }

    void synchronize(true)
    return () => {
      active = false
      controller.abort()
      if (timeoutId) window.clearTimeout(timeoutId)
    }
  }, [connected, refreshToken])

  return {
    items,
    batches,
    loading,
    error,
    refresh,
    upsert,
    remove,
  }
}
