import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { formatRelativeTime } from './format'

const NOW = new Date('2026-07-20T12:00:00.000Z')

function secondsAgo(seconds: number) {
  return new Date(NOW.getTime() - seconds * 1_000).toISOString()
}

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns a fallback for missing or invalid input', () => {
    expect(formatRelativeTime(undefined)).toBe('尚未开始')
    expect(formatRelativeTime(null)).toBe('尚未开始')
    expect(formatRelativeTime('not-a-date')).toBe('not-a-date')
  })

  it('treats future timestamps as just now', () => {
    expect(formatRelativeTime(secondsAgo(-120))).toBe('刚刚')
  })

  it('shows 刚刚 within the first minute', () => {
    expect(formatRelativeTime(secondsAgo(0))).toBe('刚刚')
    expect(formatRelativeTime(secondsAgo(59))).toBe('刚刚')
  })

  it('shows minutes below one hour', () => {
    expect(formatRelativeTime(secondsAgo(60))).toBe('1 分钟前')
    expect(formatRelativeTime(secondsAgo(59 * 60))).toBe('59 分钟前')
  })

  it('shows hours below one day', () => {
    expect(formatRelativeTime(secondsAgo(60 * 60))).toBe('1 小时前')
    expect(formatRelativeTime(secondsAgo(23 * 3600))).toBe('23 小时前')
  })

  it('shows days below thirty days', () => {
    expect(formatRelativeTime(secondsAgo(24 * 3600))).toBe('1 天前')
    expect(formatRelativeTime(secondsAgo(29 * 24 * 3600))).toBe('29 天前')
  })

  it('falls back to an absolute date at thirty days and beyond', () => {
    const fallback = formatRelativeTime(secondsAgo(30 * 24 * 3600))
    expect(fallback).not.toContain('天前')
    expect(fallback).toMatch(/^\d{2}\/\d{2} \d{2}:\d{2}:\d{2}$/)
  })
})
