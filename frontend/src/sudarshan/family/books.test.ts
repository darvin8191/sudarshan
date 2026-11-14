// SUDARSHAN P6 D4: the arithmetic and the privacy rules of the viewer's books, as plain functions.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PRICE_SCALE, QTY_SCALE } from '@/sudarshan/portfolio/types'
import {
  COST_FIELDS,
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

describe('P&L hidden with quantities shown (review H1)', () => {
  const table = JSON.parse(
    readFileSync(resolve(__dirname, '../../../../sudarshan/family/privacy_fields.json'), 'utf8')
  ) as { cost: string[] }
  const inCost = (field: string) =>
    table.cost.some((entry) =>
      entry.startsWith('*') ? field.endsWith(entry.slice(1)) : entry === field
    )

  it.each(COST_FIELDS)('%s is in the table cost list, hidden whenever P&L is', (field) => {
    expect(inCost(field)).toBe(true)
  })
  it.each(NEVER_HIDDEN)('%s is not in the cost list', (field) => {
    expect(inCost(field)).toBe(false)
  })

  // The row as the server leaves it: the quantity and the live price, no P&L, no average.
  const held: MaskedHolding = {
    exchange: 'NSE',
    symbol: 'SYNTH',
    qty_u: qty(10),
    last_price_u: px(105),
    close_price_u: px(100),
  }

  it('a holding shows its quantity and price, and no value, invested, average or day change', () => {
    const figures = holdingFigures(held, tick(106), READ_AT)
    expect(figures.quantity).toBe(10)
    expect(figures.ltp).toBe(106)
    expect(figures.dayPercent).toBeCloseTo(6)
    expect(figures.value).toBeUndefined()
    expect(figures.invested).toBeUndefined()
    expect(figures.today).toBeUndefined()
    expect(figures.average).toBeUndefined()
    expect(figures.pnl).toBeUndefined()
    expect(figures.pnlPercent).toBeUndefined()
  })

  it('works nothing out from an average a server left in by mistake', () => {
    const figures = holdingFigures({ ...held, avg_price_u: px(90) }, undefined, READ_AT)
    expect(figures.invested).toBeUndefined()
    expect(figures.average).toBeUndefined()
    expect(figures.value).toBeUndefined()
  })

  it('a position shows its quantity and direction, and no average', () => {
    const figures = positionFigures({ ...long, pnl_paise: undefined }, tick(106), READ_AT)
    expect(figures.quantity).toBe(10)
    expect(figures.side).toBe('long')
    expect(figures.average).toBeUndefined()
    expect(figures.pnl).toBeUndefined()
  })

  it('with P&L shown the same holding has all of them', () => {
    const figures = holdingFigures(
      { ...held, avg_price_u: px(90), pnl_paise: 15_000 },
      undefined,
      READ_AT
    )
    expect(figures.value).toBeCloseTo(1050)
    expect(figures.invested).toBeCloseTo(900)
    expect(figures.today).toBeCloseTo(50)
    expect(figures.average).toBe(90)
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
    // P&L shown (H1: with it hidden, no value is worked out at all), the price not known yet
    const held = holdingFigures({ ...holding, qty_u: qty(2), pnl_paise: 0 }, undefined, READ_AT)
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
