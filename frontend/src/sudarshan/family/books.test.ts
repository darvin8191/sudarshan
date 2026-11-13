// SUDARSHAN P6 D4: the arithmetic and the privacy rules of the viewer's books, as plain functions.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PRICE_SCALE, QTY_SCALE } from '@/sudarshan/portfolio/types'
import {
  FIELD_CATEGORY,
  holdingFigures,
  isOpen,
  type MaskedHolding,
  type MaskedPosition,
  NEVER_HIDDEN,
  positionFigures,
  sideOf,
  totalOf,
} from './books'

const px = (rupees: number) => Math.round(rupees * PRICE_SCALE)
const qty = (units: number) => Math.round(units * QTY_SCALE)

const READ_AT = 1_000_000
const tick = (ltp: number, at = READ_AT + 5_000, live = true) => ({
  ltp,
  prev_close: null,
  at,
  live,
})

const long: MaskedPosition = {
  exchange: 'NSE',
  symbol: 'SYNTH',
  product: 'MIS',
  qty_u: qty(10),
  avg_price_u: px(100),
  last_price_u: px(105),
  pnl_paise: 5_000,
  multiplier: 1,
}

describe('FIELD_CATEGORY against the one privacy table', () => {
  const table = JSON.parse(
    readFileSync(resolve(__dirname, '../../../../sudarshan/family/privacy_fields.json'), 'utf8')
  ) as { categories: Record<string, string[]> }

  const hides = (category: string, field: string) =>
    (table.categories[category] ?? []).some((entry) =>
      entry.startsWith('*') ? field.endsWith(entry.slice(1)) : entry === field
    )

  it.each(
    Object.entries(FIELD_CATEGORY)
  )('%s is hidden by %s and by no other', (field, category) => {
    expect(hides(category, field)).toBe(true)
    for (const other of Object.keys(table.categories)) {
      if (other !== category) {
        // qty hides what pnl needs, never the reverse: only the named category must hide it
        expect(hides(other, field) && other !== category && other !== 'qty').toBe(false)
      }
    }
  })

  it.each(NEVER_HIDDEN)('%s is hidden by no category', (field) => {
    for (const category of Object.keys(table.categories)) expect(hides(category, field)).toBe(false)
  })
})

describe('a hidden figure is never worked out and never 0', () => {
  it('a position with no quantity and no P&L has neither, and still has its price', () => {
    const masked: MaskedPosition = {
      exchange: 'NSE',
      symbol: 'SYNTH',
      product: 'MIS',
      side: 'long',
      last_price_u: px(105),
      close_price_u: px(100),
    }
    const figures = positionFigures(masked, undefined, READ_AT)
    expect(figures.quantity).toBeUndefined()
    expect(figures.pnl).toBeUndefined()
    expect(figures.average).toBeUndefined()
    expect(figures.ltp).toBe(105)
    expect(figures.dayPercent).toBeCloseTo(5)
  })

  it('a tick does not turn a hidden P&L into a number', () => {
    const masked: MaskedPosition = { ...long, qty_u: undefined, pnl_paise: undefined, side: 'long' }
    const figures = positionFigures(masked, tick(110), READ_AT)
    expect(figures.pnl).toBeUndefined()
    expect(figures.ltp).toBe(110)
  })

  it('a null P&L is not known, and is not undefined', () => {
    const figures = positionFigures(
      { ...long, pnl_paise: undefined, qty_u: qty(10) },
      undefined,
      READ_AT
    )
    expect(figures.pnl).toBeUndefined()
    const holding: MaskedHolding = { exchange: 'NSE', symbol: 'SYNTH', last_price_u: null }
    const held = holdingFigures({ ...holding, qty_u: qty(2) }, undefined, READ_AT)
    expect(held.value).toBeNull()
  })

  it('a holding with no quantity has no value, no invested and no day change', () => {
    const masked: MaskedHolding = {
      exchange: 'NSE',
      symbol: 'SYNTH',
      last_price_u: px(50),
      close_price_u: px(48),
    }
    const figures = holdingFigures(masked, undefined, READ_AT)
    expect(figures.value).toBeUndefined()
    expect(figures.invested).toBeUndefined()
    expect(figures.today).toBeUndefined()
    expect(figures.ltp).toBe(50)
  })

  it('a total over a hidden row is hidden; over an unknown row is not known; never a smaller sum', () => {
    expect(totalOf([10, undefined, 5])).toBeUndefined()
    expect(totalOf([10, null, 5])).toBeNull()
    expect(totalOf([10, 5])).toBe(15)
    expect(totalOf([])).toBe(0)
  })
})

describe('live arithmetic', () => {
  it('moves a long by quantity times the move since the read', () => {
    const figures = positionFigures(long, tick(106), READ_AT)
    // 10 units x Rs 1 move = Rs 10 = 1000 paise on top of 5000 paise
    expect(figures.pnl).toBeCloseTo(60)
    expect(figures.moved).toBe(true)
  })

  it('moves a short the other way', () => {
    const short = { ...long, qty_u: qty(-10), pnl_paise: 0 }
    expect(sideOf(short)).toBe('short')
    expect(positionFigures(short, tick(106), READ_AT).pnl).toBeCloseTo(-10)
  })

  it('applies the contract multiplier', () => {
    const future = { ...long, multiplier: 25, pnl_paise: 0 }
    expect(positionFigures(future, tick(106), READ_AT).pnl).toBeCloseTo(250)
  })

  it('ignores a tick that is not newer than the read, or not live', () => {
    expect(positionFigures(long, tick(110, READ_AT), READ_AT).pnl).toBeCloseTo(50)
    expect(positionFigures(long, tick(110, READ_AT + 9, false), READ_AT).pnl).toBeCloseTo(50)
  })

  it('moves a holding value and P&L at the tick', () => {
    const holding: MaskedHolding = {
      exchange: 'NSE',
      symbol: 'SYNTH',
      qty_u: qty(4),
      avg_price_u: px(50),
      last_price_u: px(55),
      pnl_paise: 2_000,
      close_price_u: px(54),
    }
    const figures = holdingFigures(holding, tick(56), READ_AT)
    expect(figures.value).toBeCloseTo(224)
    expect(figures.invested).toBeCloseTo(200)
    expect(figures.pnl).toBeCloseTo(24)
    expect(figures.today).toBeCloseTo(8)
  })
})

describe('direction', () => {
  it('the sign of the quantity decides over a stale side', () => {
    expect(sideOf({ qty_u: qty(-3), side: 'long' })).toBe('short')
  })
  it('without a quantity the service side decides, and closed is not open', () => {
    expect(sideOf({ side: 'short' })).toBe('short')
    expect(isOpen({ side: 'closed' })).toBe(false)
    expect(isOpen({})).toBe(false)
  })
})
