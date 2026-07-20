import { useCallback, useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, RefObject } from 'react'

const STORAGE_KEY = 'captionnest.columnLayout.v1'

const DEFAULT_RATIOS = { nav: 0.2, list: 0.5, detail: 0.3 } as const
const MIN_WIDTHS = { nav: 120, list: 320, detail: 340 } as const
const MIN_TOTAL = MIN_WIDTHS.nav + MIN_WIDTHS.list + MIN_WIDTHS.detail
const HANDLE_COUNT = 2
const HANDLE_WIDTH = 6
const DESKTOP_MEDIA_QUERY = '(min-width: 921px)'

export type ColumnHandle = 'nav' | 'list'

interface ColumnRatios {
  nav: number
  list: number
  detail: number
}

interface ColumnWidths {
  nav: number
  list: number
  detail: number
}

function ratioValue(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value < 1
    ? value
    : fallback
}

export function parseStoredRatios(text: string | null): ColumnRatios {
  try {
    const stored = JSON.parse(text ?? 'null') as {
      version?: number
      ratios?: Record<string, unknown>
    } | null
    if (!stored || stored.version !== 1 || !stored.ratios) return { ...DEFAULT_RATIOS }
    const ratios = {
      nav: ratioValue(stored.ratios.nav, DEFAULT_RATIOS.nav),
      list: ratioValue(stored.ratios.list, DEFAULT_RATIOS.list),
      detail: ratioValue(stored.ratios.detail, DEFAULT_RATIOS.detail),
    }
    const total = ratios.nav + ratios.list + ratios.detail
    return { nav: ratios.nav / total, list: ratios.list / total, detail: ratios.detail / total }
  } catch {
    return { ...DEFAULT_RATIOS }
  }
}

function loadRatios(): ColumnRatios {
  if (typeof window === 'undefined') return { ...DEFAULT_RATIOS }
  return parseStoredRatios(window.localStorage.getItem(STORAGE_KEY))
}

function resolveWidths(available: number, ratios: ColumnRatios): ColumnWidths {
  if (available <= 0) {
    return { nav: MIN_WIDTHS.nav, list: MIN_WIDTHS.list, detail: MIN_WIDTHS.detail }
  }
  if (available < MIN_TOTAL) {
    const scale = available / MIN_TOTAL
    const nav = Math.floor(MIN_WIDTHS.nav * scale)
    const list = Math.floor(MIN_WIDTHS.list * scale)
    return { nav, list, detail: Math.max(0, available - nav - list) }
  }
  const nav = Math.min(
    Math.max(Math.round(available * ratios.nav), MIN_WIDTHS.nav),
    available - MIN_WIDTHS.list - MIN_WIDTHS.detail,
  )
  const list = Math.min(
    Math.max(Math.round(available * ratios.list), MIN_WIDTHS.list),
    available - nav - MIN_WIDTHS.detail,
  )
  return { nav, list, detail: available - nav - list }
}

function shiftedRatios(
  handle: ColumnHandle,
  widths: ColumnWidths,
  delta: number,
  available: number,
): ColumnRatios {
  if (handle === 'nav') {
    const nav = Math.min(
      Math.max(widths.nav + delta, MIN_WIDTHS.nav),
      widths.nav + widths.list - MIN_WIDTHS.list,
    )
    const list = widths.nav + widths.list - nav
    return { nav: nav / available, list: list / available, detail: widths.detail / available }
  }
  const list = Math.min(
    Math.max(widths.list + delta, MIN_WIDTHS.list),
    widths.list + widths.detail - MIN_WIDTHS.detail,
  )
  const detail = widths.list + widths.detail - list
  return { nav: widths.nav / available, list: list / available, detail: detail / available }
}

export function useColumnLayout(containerRef: RefObject<HTMLElement | null>) {
  const [ratios, setRatios] = useState<ColumnRatios>(loadRatios)
  const [containerWidth, setContainerWidth] = useState(0)
  const [enabled, setEnabled] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(DESKTOP_MEDIA_QUERY).matches)
  const [resizing, setResizing] = useState<ColumnHandle | null>(null)
  const dragState = useRef<{ handle: ColumnHandle; startX: number; widths: ColumnWidths } | null>(null)

  useEffect(() => {
    const media = window.matchMedia(DESKTOP_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setEnabled(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setContainerWidth(entry.contentRect.width)
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [containerRef])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ratios }))
    } catch {
      // Layout persistence is a convenience. A blocked WebView store must not break dragging.
    }
  }, [ratios])

  const available = Math.max(0, containerWidth - HANDLE_COUNT * HANDLE_WIDTH)
  const widths = resolveWidths(available, ratios)
  const adjustable = enabled && available >= MIN_TOTAL

  const startResize = useCallback((handle: ColumnHandle) => (event: ReactPointerEvent) => {
    if (event.button !== 0 || !adjustable) return
    event.preventDefault()
    const target = event.currentTarget as HTMLElement
    target.setPointerCapture(event.pointerId)
    dragState.current = { handle, startX: event.clientX, widths }
    setResizing(handle)
  }, [adjustable, widths])

  const onHandlePointerMove = useCallback((event: ReactPointerEvent) => {
    const drag = dragState.current
    if (!drag || available < MIN_TOTAL) return
    const delta = event.clientX - drag.startX
    setRatios(shiftedRatios(drag.handle, drag.widths, delta, available))
  }, [available])

  const onHandlePointerEnd = useCallback((event: ReactPointerEvent) => {
    if (!dragState.current) return
    dragState.current = null
    setResizing(null)
    const target = event.currentTarget as HTMLElement
    if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId)
  }, [])

  const resizeBy = useCallback((handle: ColumnHandle, delta: number) => {
    if (available < MIN_TOTAL) return
    setRatios((current) => shiftedRatios(handle, resolveWidths(available, current), delta, available))
  }, [available])

  const resetRatios = useCallback(() => setRatios({ ...DEFAULT_RATIOS }), [])

  return {
    widths,
    enabled,
    resizing,
    startResize,
    onHandlePointerMove,
    onHandlePointerEnd,
    resizeBy,
    resetRatios,
  }
}
