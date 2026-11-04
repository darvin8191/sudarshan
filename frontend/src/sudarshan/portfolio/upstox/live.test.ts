/** The Upstox figures moved by ticks (design 3.4): anchors, signs and what does not move. */
import { describe, expect, it } from 'vitest'
import type { Tick } from '../liveTab'
import { holding, position, READ_AT } from './fixtures'
import { holdingNow, holdingsTotals, movedPriceU, positionNow } from './live'

const tick = (ltp: number, extra: Partial<Tick> = {}): Tick => ({
  ltp,
  prev_close: null,
  at: READ_AT + 1000,
  live: true,
  ...extra,
})

describe('which ticks move a row', () => {
  it('only a real-time tick newer than the read', () => {
    expect(movedPriceU(tick(1510), READ_AT)).toBe(15_100_000)
    expect(movedPriceU(tick(1510, { at: READ_AT }), READ_AT)).toBeNull() // the read had it
    expect(movedPriceU(tick(1510, { at: READ_AT - 1 }), READ_AT)).toBeNull()
    expect(movedPriceU(tick(1510, { live: false }), READ_AT)).toBeNull() // a fallback answer
    expect(movedPriceU(undefined, READ_AT)).toBeNull()
    expect(movedPriceU(tick(1510), null)).toBeNull()
  })
})

describe('a holding', () => {
  it('is Upstox own figures until a tick comes', () => {
    const now = holdingNow(holding(), undefined, READ_AT)
    expect(now.moved).toBe(false)
    expect(now.valuePaise).toBe(1_500_000) // 10 x 1,500
    expect(now.pnlPaise).toBe(100_000) // Upstox pnl, not recomputed
    expect(now.todayPaise).toBe(20_000) // 10 x (1,500 - 1,480)
  })

  it('moves value, P&L and today by the tick, from the read price', () => {
    const now = holdingNow(holding(), tick(1510), READ_AT)
    expect(now.moved).toBe(true)
    expect(now.valuePaise).toBe(1_510_000)
    expect(now.pnlPaise).toBe(100_000 + 10_000) // + 10 x (1,510 - 1,500)
    expect(now.todayPaise).toBe(30_000)
  })

  it('keeps its sign when the tick is below the read price', () => {
    const now = holdingNow(holding(), tick(1490), READ_AT)
    expect(now.pnlPaise).toBe(100_000 - 10_000)
    expect(now.todayPaise).toBe(10_000)
    expect(holdingNow(holding(), tick(1400), READ_AT).todayPaise).toBe(-80_000)
  })

  it('has no Today without a previous close, never zero', () => {
    const now = holdingNow(holding({ close_price_u: null }), tick(1510), READ_AT)
    expect(now.todayPaise).toBeNull()
    expect(now.todayPct).toBeNull()
  })

  it('sums the rows and counts those with no Today', () => {
    const a = holdingNow(holding(), undefined, READ_AT)
    const b = holdingNow(holding({ close_price_u: null }), undefined, READ_AT)
    const totals = holdingsTotals([a, b])
    expect(totals.valuePaise).toBe(3_000_000)
    expect(totals.pnlPaise).toBe(200_000)
    expect(totals.todayPaise).toBe(20_000)
    expect(totals.todayUnknown).toBe(1)
    expect(holdingsTotals([b]).todayPaise).toBeNull()
  })
})

describe('a position', () => {
  it('a long gains as the price rises', () => {
    expect(positionNow(position(), tick(112), READ_AT).pnlPaise).toBe(65_000 + 13_000)
  })

  it('a short loses as the price rises, multiplier included', () => {
    const short = position({ qty_u: -10_000_000, multiplier: 2, pnl_paise: 0, unrealised_paise: 0 })
    const now = positionNow(short, tick(111), READ_AT)
    expect(now.pnlPaise).toBe(-2000) // -10 x (111 - 110) x 2
    expect(now.unrealisedPaise).toBe(-2000)
  })

  it('a closed position does not move', () => {
    const closed = position({ qty_u: 0, pnl_paise: 5000, unrealised_paise: 0 })
    expect(positionNow(closed, tick(130), READ_AT).pnlPaise).toBe(5000)
  })
})
