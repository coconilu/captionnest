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
