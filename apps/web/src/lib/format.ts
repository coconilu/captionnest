import type { JobLog } from '../types/api'

export function formatBytes(value?: number) {
  if (!value && value !== 0) return '大小未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit > 1 ? 2 : 0)} ${units[unit]}`
}

export function fileNameFromPath(path: string) {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path
}

export function formatLog(log: JobLog | string): JobLog {
  return typeof log === 'string' ? { message: log, level: 'info' } : log
}

export function formatTime(timestamp?: string) {
  if (!timestamp) return '刚刚'
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return timestamp
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatDateTime(timestamp?: string | null) {
  if (!timestamp) return '尚未结束'
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return timestamp
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatRelativeTime(timestamp?: string | null) {
  if (!timestamp) return '尚未开始'
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return timestamp
  const diffMs = Date.now() - date.getTime()
  if (diffMs < 0) return '刚刚'
  const seconds = Math.floor(diffMs / 1_000)
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return formatDateTime(timestamp)
}

export function formatBatchStamp(timestamp?: string | null) {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatDuration(durationMs?: number | null) {
  if (durationMs === null || durationMs === undefined) return '未记录'
  if (durationMs < 1_000) return `${durationMs} 毫秒`
  if (durationMs < 60_000) {
    const digits = durationMs < 10_000 ? 2 : 1
    return `${(durationMs / 1_000).toFixed(digits)} 秒`
  }
  const totalSeconds = Math.round(durationMs / 1_000)
  const hours = Math.floor(totalSeconds / 3_600)
  const minutes = Math.floor((totalSeconds % 3_600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return `${hours} 小时 ${minutes} 分 ${seconds} 秒`
  return `${minutes} 分 ${seconds} 秒`
}

export function formatTokenCount(value?: number | null) {
  if (value === null || value === undefined) return '未报告'
  return new Intl.NumberFormat('zh-CN').format(value)
}
