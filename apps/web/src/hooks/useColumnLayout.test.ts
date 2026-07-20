import { describe, expect, it } from 'vitest'

import { parseStoredRatios } from './useColumnLayout'

describe('parseStoredRatios', () => {
  it('returns defaults for missing or malformed input', () => {
    expect(parseStoredRatios(null)).toEqual({ nav: 0.2, list: 0.5, detail: 0.3 })
    expect(parseStoredRatios('not json')).toEqual({ nav: 0.2, list: 0.5, detail: 0.3 })
    expect(parseStoredRatios('{"version":2,"ratios":{"nav":0.9}}'))
      .toEqual({ nav: 0.2, list: 0.5, detail: 0.3 })
    expect(parseStoredRatios('{"version":1}')).toEqual({ nav: 0.2, list: 0.5, detail: 0.3 })
  })

  it('drops illegal ratio values in favor of defaults', () => {
    const parsed = parseStoredRatios(
      JSON.stringify({ version: 1, ratios: { nav: -0.5, list: 2, detail: 'wide' } }),
    )
    expect(parsed).toEqual({ nav: 0.2, list: 0.5, detail: 0.3 })
  })

  it('normalizes persisted ratios so they sum to one', () => {
    const parsed = parseStoredRatios(
      JSON.stringify({ version: 1, ratios: { nav: 0.4, list: 0.4, detail: 0.4 } }),
    )
    expect(parsed.nav + parsed.list + parsed.detail).toBeCloseTo(1, 10)
    expect(parsed.nav).toBeCloseTo(1 / 3, 10)
  })
})
