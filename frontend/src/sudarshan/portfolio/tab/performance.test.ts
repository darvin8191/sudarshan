import { describe, expect, it } from 'vitest'
import { chartDay, withToday } from './performance'

const S = [
  { day: 20261102, value_paise: 100, net_put_in_paise: 90 },
  { day: 20261103, value_paise: 110, net_put_in_paise: 90 },
]

describe('the value series with today', () => {
  it('adds today after the last day from the live value and the net put in', () => {
    expect(withToday(S, 20261104, 125, 95).at(-1)).toEqual({
      day: 20261104,
      value_paise: 125,
      net_put_in_paise: 95,
    })
  })
  it('replaces the point of today, keeping its net put in', () => {
    const out = withToday(S, 20261103, 130, 95)
    expect(out).toHaveLength(2)
    expect(out[1]).toEqual({ day: 20261103, value_paise: 130, net_put_in_paise: 90 })
  })
  it('draws the series as it is with no live value, or when it already passed today', () => {
    expect(withToday(S, 20261104, null, 95)).toEqual(S)
    expect(withToday(S, 20261101, 130, 95)).toEqual(S)
    expect(withToday([], 20261104, 130, 95)).toEqual([])
  })
  it('writes a day as the chart reads it', () => {
    expect(chartDay(20261104)).toBe('2026-11-04')
  })
})
