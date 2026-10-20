import { describe, expect, it } from 'vitest'
import type { PriceSource } from '@/sudarshan/books/prices'
import type { Holding, Order, Position } from '@/types/trading'
import { clockOf, figure, momentOf, orderState, sideOf, signedUnits, units } from './format'
import { dayChangeOf } from './HoldingCards'
import { filledText, priceText, remainingText } from './OrderCards'
import { addProduct, realisedOf, splitAddsUp, splitOf, unrealisedOf } from './PositionCards'
import type { PricedRow } from './useFramePrices'

function priced<T>(item: T, figures: Partial<PricedRow<T>> = {}): PricedRow<T> {
  return {
    item,
    key: '',
    ltp: null,
    pnl: null,
    pnlPercent: null,
    at: null,
    live: false,
    source: 'rest' as PriceSource,
    tick: null,
    prevClose: null,
    tickAt: null,
    ...figures,
  }
}

describe('book figures in words', () => {
  it('reads a figure the broker may send as text, or not at all', () => {
    expect(figure(65)).toBe(65)
    expect(figure('142.50')).toBe(142.5)
    expect(figure('')).toBeNull()
    expect(figure(null)).toBeNull()
    expect(figure(undefined)).toBeNull()
    expect(figure('lots')).toBeNull()
  })

  it('writes a quantity with its sign and the side in words', () => {
    expect(units(1050)).toBe('1,050')
    expect(units(0.25)).toBe('0.25')
    expect(signedUnits(65)).toBe('+65')
    expect(signedUnits(-1050)).toBe('−1,050')
    expect(signedUnits(0)).toBe('0')
    expect(sideOf(65)).toBe('LONG')
    expect(sideOf(-30)).toBe('SHORT')
    expect(sideOf(0)).toBeNull()
  })

  it('names every status in the book’s words, with what can still be done', () => {
    expect(orderState('complete')).toMatchObject({ label: 'COMPLETE', tone: 'up', working: false })
    expect(orderState('REJECTED')).toMatchObject({
      label: 'REJECTED',
      tone: 'down',
      filter: 'rejected',
    })
    expect(orderState('cancelled')).toMatchObject({ label: 'CANCELLED', tone: 'muted' })
    expect(orderState('open')).toMatchObject({ label: 'OPEN', tone: 'working', working: true })
    expect(orderState('trigger pending')).toMatchObject({
      label: 'TRIGGER PENDING',
      filter: 'open',
      working: true,
    })
    expect(orderState('pending')).toMatchObject({ label: 'PENDING', working: false })
    // A word the book does not know is shown as the broker said it, with nothing to do on it.
    expect(orderState('validation pending')).toMatchObject({
      label: 'VALIDATION PENDING',
      tone: 'muted',
      filter: null,
      working: false,
    })
  })

  it('reads a broker’s timestamp as a time of day and as a moment to sort by', () => {
    expect(clockOf('2026-10-20 10:41:51')).toBe('10:41:51')
    expect(clockOf('20-Oct-2026 9:05:07')).toBe('09:05:07')
    expect(clockOf('10:41:51 20-10-2026')).toBe('10:41:51')
    expect(clockOf('')).toBe('')
    expect(momentOf('2026-10-20 10:41:51')).toBeGreaterThan(momentOf('2026-10-20 10:12:18'))
    expect(momentOf('10:41:51 20-10-2026')).toBe(momentOf('2026-10-20 10:41:51'))
    expect(momentOf('20-10-2026 10:41:51')).toBe(momentOf('2026-10-20 10:41:51'))
    expect(momentOf('')).toBe(0)
  })
})

describe('an order as its card says it', () => {
  const order = (fields: Partial<Order> & Record<string, unknown>) =>
    ({
      symbol: 'NIFTY27OCT2625900CE',
      exchange: 'NFO',
      action: 'SELL',
      quantity: 65,
      price: 0,
      trigger_price: 0,
      pricetype: 'LIMIT',
      product: 'MIS',
      orderid: '1',
      order_status: 'open',
      timestamp: '',
      ...fields,
    }) as Order

  it('says filled of ordered once something filled, the quantity alone otherwise', () => {
    expect(filledText(order({ filled_quantity: 20 }))).toEqual({ text: '20/65', filled: true })
    expect(filledText(order({ order_status: 'complete' }))).toEqual({ text: '65/65', filled: true })
    // Upstox's REST book carries no fills: nothing known, nothing claimed.
    expect(filledText(order({}))).toEqual({ text: '65', filled: false })
    // Never "0/65".
    expect(filledText(order({ filled_quantity: 0 }))).toEqual({ text: '65', filled: false })
    expect(filledText(order({ order_status: 'cancelled', filled_quantity: 0 }))).toEqual({
      text: '65',
      filled: false,
    })
    expect(filledText(order({ order_status: 'cancelled', filled_quantity: 40 }))).toEqual({
      text: '40/65',
      filled: true,
    })
  })

  it('names what a cancel stops: the rest of a part-filled order', () => {
    expect(remainingText(order({ quantity: 100, filled_quantity: 40 }))).toBe(
      'the remaining 60 of 100'
    )
    expect(remainingText(order({ quantity: 100, filled_quantity: 40, pending_quantity: 60 }))).toBe(
      'the remaining 60 of 100'
    )
    expect(remainingText(order({}))).toBe('65')
    expect(remainingText(order({ filled_quantity: 0 }))).toBe('65')
  })

  it('shows an average only when one is known', () => {
    expect(priceText(order({ price: 168 }))).toBe('Limit 168.00')
    expect(priceText(order({ price: 168, average_price: 0 }))).toBe('Limit 168.00')
    expect(priceText(order({ price: 168, average_price: 168.05 }))).toBe(
      'Limit 168.00 · Avg 168.05'
    )
  })

  it('says what price the order works at, and the average once it filled', () => {
    expect(priceText(order({ price: 168 }))).toBe('Limit 168.00')
    expect(priceText(order({ pricetype: 'MARKET' }))).toBe('Market')
    expect(priceText(order({ pricetype: 'SL', price: 95, trigger_price: 96 }))).toBe(
      'Trigger 96.00 · Limit 95.00'
    )
    expect(priceText(order({ pricetype: 'SL-M', trigger_price: 96 }))).toBe('SL-M · Trigger 96.00')
    expect(priceText(order({ pricetype: 'MARKET', average_price: 142.5 }))).toBe(
      'Market · Avg 142.50'
    )
    expect(priceText(order({ exchange: 'CDS', price: 83.2525 }))).toBe('Limit 83.2525')
  })
})

describe('a position’s P&L, booked and open', () => {
  const position = (fields: Record<string, unknown>) =>
    ({
      symbol: 'NIFTY27OCT2625900CE',
      exchange: 'NFO',
      product: 'MIS',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 1950,
      ...fields,
    }) as unknown as Position

  it('takes the broker’s realised figure, or the sandbox’s, or all of a closed one', () => {
    expect(realisedOf(position({ realised: 1300 }))).toBe(1300)
    expect(realisedOf(position({ today_realized_pnl: 1300 }))).toBe(1300)
    expect(realisedOf(position({ quantity: 0, pnl: 1200 }))).toBe(1200)
    expect(realisedOf(position({}))).toBeNull()
  })

  it('moves the broker’s unrealised figure by what the price did since the book was read', () => {
    const held = position({ realised: 1300, unrealised: 650 })
    // The book said 1,950; ticks moved it to 2,210.
    expect(unrealisedOf(priced(held, { pnl: 2210 }))).toBe(910)
    // The sandbox's name for it.
    expect(unrealisedOf(priced(position({ unrealized_pnl: 650 }), { pnl: 2210 }))).toBe(910)
    // No unrealised figure: what is not booked.
    expect(unrealisedOf(priced(position({ realised: 1300 }), { pnl: 2210 }))).toBe(910)
    expect(unrealisedOf(priced(position({}), { pnl: 2210 }))).toBeNull()
    expect(unrealisedOf(priced(position({ quantity: 0 }), { pnl: 1200 }))).toBe(0)
  })

  it('splits the P&L only where booked and open add up to it, within a rupee', () => {
    // Opened today: 1,300 booked + 650 open = 1,950.
    expect(splitAddsUp(position({ realised: 1300, unrealised: 650 }))).toBe(true)
    // The broker's rounding is not a disagreement.
    expect(splitAddsUp(position({ realised: 1300.4, unrealised: 650.45 }))).toBe(true)
    // Carried in: the day's 0 + 754 is not the net 1,950.
    expect(splitAddsUp(position({ realised: 0, unrealised: 754 }))).toBe(false)
    // An open part worked out as the P&L less what was booked adds up by itself.
    expect(splitAddsUp(position({ realised: 1300 }))).toBe(true)
    // Nothing booked said: nothing to split.
    expect(splitAddsUp(position({ unrealised: 1950 }))).toBe(false)
  })

  it('splits the whole book only when every position adds up', () => {
    const today = priced(position({ realised: 1300, unrealised: 650 }), { pnl: 1950 })
    const carried = priced(position({ realised: 0, unrealised: 754 }), { pnl: 1950 })
    expect(splitOf([today])).toEqual({ realised: 1300, unrealised: 650 })
    expect(splitOf([today, carried])).toBeNull()
  })

  it('adds on the position’s own product, so the order adds to it', () => {
    expect(addProduct(position({ product: 'NRML' }))).toBe('NRML')
    expect(addProduct(position({ product: 'MIS' }))).toBe('MIS')
    expect(addProduct(position({ exchange: 'NSE', product: 'CNC', symbol: 'SBIN' }))).toBe('CNC')
    // A product the segment's ticket does not take falls back to the ticket's own rule.
    expect(addProduct(position({ product: 'CNC' }))).toBe('MIS')
  })
})

describe('the holdings’ change on the day', () => {
  const holding = (symbol: string, quantity: number) =>
    ({ symbol, exchange: 'NSE', quantity, product: 'CNC', pnl: 0, pnlpercent: 0 }) as Holding

  it('measures from each holding’s previous close, once every one has it', () => {
    const rows = [
      priced(holding('RELIANCE', 40), { ltp: 1452.3, prevClose: 1461 }),
      priced(holding('HDFCBANK', 60), { ltp: 982.65, prevClose: 975.2 }),
    ]
    const day = dayChangeOf(rows)
    expect(day?.amount).toBeCloseTo(99, 6)
    expect(day?.percent).toBeCloseTo((99 / 116952) * 100, 6)
    expect(dayChangeOf([...rows, priced(holding('TCS', 5), { ltp: 3045.6 })])).toBeNull()
  })
})
