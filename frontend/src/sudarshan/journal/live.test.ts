/**
 * The open-trade arithmetic against the vectors the Python core runs (sudarshan/journal/tests/
 * vectors/metrics.json, `live` L1-L5: one copy, read from there), and the journal's words.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { closedTrip, trip } from './fixtures'
import {
  dayWords,
  excursionBar,
  groupByDay,
  holdWords,
  livePnlPaise,
  openQty,
  outcomeOf,
  priceText,
  priceU,
  strategyOf,
} from './live'
import type { Side } from './types'

interface Live {
  id: string
  side: Side
  qty: number
  entry_avg_u: number
  ticks: Array<{ ltp_u: number; pnl_paise: number }>
}

const VECTORS = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'journal', 'tests', 'vectors', 'metrics.json'),
    'utf8'
  )
).live as Live[]

describe('livePnlPaise against the L vectors', () => {
  it('reads all five cases', () => {
    expect(VECTORS.map((v) => v.id)).toEqual(['L1', 'L2', 'L3', 'L4', 'L5'])
  })

  for (const vector of VECTORS) {
    it(`${vector.id}: ${vector.ticks.length} ticks`, () => {
      for (const tick of vector.ticks) {
        expect(livePnlPaise(vector.side, vector.qty, vector.entry_avg_u, tick.ltp_u)).toBe(
          tick.pnl_paise
        )
      }
    })
  }

  it('never answers a negative zero', () => {
    expect(Object.is(livePnlPaise('short', 1, 1_000_000, 1_000_049), 0)).toBe(true)
    expect(Object.is(livePnlPaise('long', 1, 1_000_000, 999_951), 0)).toBe(true)
  })
})

describe('the words and groups', () => {
  it('turns a feed price into price_u without a float artefact', () => {
    expect(priceU(83.0003)).toBe(830003)
    expect(priceU(101.5)).toBe(1_015_000)
    expect(priceU(0)).toBeNull()
    expect(priceU(Number.NaN)).toBeNull()
  })

  it('shows a price with 2 places, 4 when a currency tick needs them', () => {
    expect(priceText(1_000_000)).toBe('100.00')
    expect(priceText(830_001)).toBe('83.0001')
    expect(priceText(12_345_600)).toBe('1,234.56')
  })

  it('says how long a trade was held', () => {
    expect(holdWords(30_000)).toBe('30 s')
    expect(holdWords(5 * 60_000)).toBe('5 min')
    expect(holdWords((2 * 60 + 14) * 60_000)).toBe('2 h 14 min')
    expect(holdWords(3 * 3_600_000)).toBe('3 h')
    expect(holdWords((3 * 24 + 4) * 3_600_000)).toBe('3 d 4 h')
    expect(holdWords(-5)).toBe('0 s')
  })

  it('writes a day as the owner reads it', () => {
    expect(dayWords(20261113)).toBe('Fri 13 Nov 2026')
  })

  it('counts what is still open after a part close, never below zero', () => {
    expect(openQty({ entry_qty: 65, exit_qty: 25 })).toBe(40)
    expect(openQty({ entry_qty: 65, exit_qty: 80 })).toBe(0)
  })

  it('groups closed trades by the day they closed, newest first, with the day’s net', () => {
    const groups = groupByDay([
      closedTrip({ id: 1, net_paise: 1000, close_day: 20261112, closed_ms: 100 }),
      closedTrip({ id: 2, net_paise: -400, close_day: 20261113, closed_ms: 200 }),
      closedTrip({ id: 3, net_paise: 900, close_day: 20261113, closed_ms: 300 }),
    ])
    expect(groups.map((g) => g.day)).toEqual([20261113, 20261112])
    expect(groups[0].trips.map((t) => t.id)).toEqual([3, 2])
    expect(groups[0].net_paise).toBe(500)
    expect(groups[1].net_paise).toBe(1000)
  })

  it('counts the trades the day’s sum leaves out, and says when it rests on an estimate (review M7)', () => {
    const [day] = groupByDay([
      closedTrip({
        id: 1,
        net_paise: 1000,
        close_day: 20261113,
        closed_ms: 3,
        charges_actual_paise: 49,
      }),
      {
        ...closedTrip({ id: 2, net_paise: 0, close_day: 20261113, closed_ms: 2 }),
        net_paise: null,
      },
      closedTrip({ id: 3, net_paise: -400, close_day: 20261113, closed_ms: 1 }),
    ])
    expect(day.net_paise).toBe(600)
    expect(day.missing).toBe(1)
    expect(day.estimated).toBe(true)
    const [actual] = groupByDay([
      closedTrip({ id: 4, net_paise: 1000, close_day: 20261112, charges_actual_paise: 49 }),
    ])
    expect(actual.estimated).toBe(false)
    expect(actual.missing).toBe(0)
  })

  it('a day with no net yet has no sum, rather than a zero', () => {
    const [group] = groupByDay(
      [closedTrip({ id: 1, net_paise: 0, close_day: 20261113, closed_ms: 5 })].map((t) => ({
        ...t,
        net_paise: null,
      }))
    )
    expect(group.net_paise).toBeNull()
  })

  it('names the owner’s strategy over the recorded one', () => {
    expect(strategyOf({ strategy: 'Flow: gap', strategy_override: null })).toBe('Flow: gap')
    expect(strategyOf({ strategy: 'Flow: gap', strategy_override: 'ORB' })).toBe('ORB')
  })

  it('calls a result profit, loss or flat', () => {
    expect([outcomeOf(1), outcomeOf(-1), outcomeOf(0)]).toEqual(['profit', 'loss', 'flat'])
  })

  it('draws the excursion bar only when it was computed', () => {
    expect(excursionBar(trip({ id: 1 }))).toBeNull()
    const bar = excursionBar({ mae_paise: -19500, mfe_paise: 97500, net_paise: 81250 })
    expect(bar?.zeroAt).toBeCloseTo(16.67, 1)
    expect(bar?.netAt).toBeCloseTo(((81250 + 19500) / 117000) * 100, 5)
    expect(excursionBar({ mae_paise: 0, mfe_paise: 0, net_paise: 0 })).toBeNull()
  })
})
