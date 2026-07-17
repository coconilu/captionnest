import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface CreateTaskDialogProps {
  open: boolean
  busy: boolean
  children: ReactNode
  onRequestClose: () => void
}

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'a[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableElements(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)]
    .filter((element) => element.getClientRects().length > 0)
}

export function CreateTaskDialog({
  open,
  busy,
  children,
  onRequestClose,
}: CreateTaskDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return undefined

    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const appShell = document.querySelector<HTMLElement>('.app-shell')
    const body = document.body
    const previousInert = appShell?.getAttribute('inert') ?? null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth

    appShell?.setAttribute('inert', '')
    body.style.overflow = 'hidden'
    if (scrollbarWidth > 0) {
      const currentPadding = Number.parseFloat(window.getComputedStyle(body).paddingRight) || 0
      body.style.paddingRight = `${currentPadding + scrollbarWidth}px`
    }

    const animationFrame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current
      const firstFocusable = dialog ? focusableElements(dialog)[0] : null
      ;(firstFocusable ?? dialog)?.focus({ preventScroll: true })
    })

    return () => {
      window.cancelAnimationFrame(animationFrame)
      if (appShell) {
        if (previousInert === null) appShell.removeAttribute('inert')
        else appShell.setAttribute('inert', previousInert)
      }
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      const returnFocus = returnFocusRef.current?.isConnected
        ? returnFocusRef.current
        : document.querySelector<HTMLElement>('[data-create-task-trigger="toolbar"]')
      returnFocus?.focus({ preventScroll: true })
      returnFocusRef.current = null
    }
  }, [open])

  useEffect(() => {
    if (!open) return undefined

    const handleKeyDown = (event: KeyboardEvent) => {
      const dialog = dialogRef.current
      if (!dialog) return
      if (event.key === 'Escape') {
        if (!busy) {
          event.preventDefault()
          onRequestClose()
        }
        return
      }
      if (event.key !== 'Tab') return

      const focusable = focusableElements(dialog)
      if (!focusable.length) {
        event.preventDefault()
        dialog.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown, true)
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [busy, onRequestClose, open])

  if (!open) return null

  return createPortal(
    <div
      className="create-task-dialog-layer"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) event.preventDefault()
      }}
    >
      <div
        ref={dialogRef}
        className="create-task-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-task-dialog-title"
        aria-describedby="create-task-dialog-description"
        tabIndex={-1}
      >
        <header className="create-task-dialog-header">
          <div>
            <h2 id="create-task-dialog-title">新建任务</h2>
            <p id="create-task-dialog-description">添加视频并设置处理配置</p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onRequestClose}
            disabled={busy}
            aria-label={busy ? '任务创建处理中，暂时无法关闭' : '关闭新建任务弹窗'}
          >
            <X size={19} />
          </button>
        </header>
        <div className="create-task-dialog-body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
