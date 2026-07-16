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
