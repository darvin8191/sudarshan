/**
 * How a book row is priced (M6.3): the newest source wins, and an open row's
 * P&L is the broker's own figure moved by the price change since the book
 * was read. The defect this replaces: upstream rebuilt the P&L from the
 * average price plus `today_realized_pnl`, a field only the sandbox sends, so
 * a partly booked Upstox position lost its booked profit on the first tick.
 */
import { describe, expect, it } from 'vitest'
import { positionPnlAt } from '@/hooks/useLivePrice'
import type { SymbolData } from '@/lib/MarketDataManager'
import { priceRow, TickDirections, tickCount, tickPrevClose } from './prices'

const BOOK_AT = 1_000_000

function tick(ltp: number, at: number, source: 'websocket' | 'rest' = 'websocket'): SymbolData {
  return { symbol: 'X', exchange: 'NFO', data: { ltp }, lastUpdate: at, updateSource: source }
}

describe('priceRow: the broker P&L moved by ticks', () => {
  it('keeps the booked profit of a partly booked long and adds what the rest makes', () => {
    // Bought 130 at 120, sold 65 at 140 (+1,300 booked), 65 still held. The
    // broker reports pnl = 1,300 + (130 - 120) x 65 = 1,950 at ltp 130.
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 1950,
    }
    const priced = priceRow(row, { tick: tick(134, BOOK_AT + 500), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.ltp).toBe(134)
    expect(priced.pnl).toBeCloseTo(1950 + 4 * 65)
    // Upstream's figure would have been (134 - 120) x 65 = 910: the booked 1,300 gone.
    expect(priced.pnl).not.toBeCloseTo(910)
    expect(priced.live).toBe(true)
  })

  it('keeps the booked profit of a partly booked short and gains as the price falls', () => {
    // Sold 130 at 200, bought back 65 at 180 (+1,300 booked), short 65 at ltp 190.
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: -65,
      average_price: 200,
      ltp: 190,
      pnl: 1950,
    }
    const priced = priceRow(row, { tick: tick(185, BOOK_AT + 500), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.pnl).toBeCloseTo(1950 + 5 * 65)
    const risen = priceRow(row, { tick: tick(195, BOOK_AT + 600), bookAt: BOOK_AT }, positionPnlAt)
    expect(risen.pnl).toBeCloseTo(1950 - 5 * 65)
  })

  it('moves a carried position from the broker figure, not from its old average', () => {
    // Bought yesterday at 100; the broker counts today's P&L from yesterday's
    // close of 105, so at 110 it reports (110 - 105) x 60 = 300, not 600.
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 60,
      average_price: 100,
      ltp: 110,
      pnl: 300,
    }
    const priced = priceRow(row, { tick: tick(112, BOOK_AT + 1), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.pnl).toBeCloseTo(300 + 2 * 60)
  })

  it('counts an F&O quantity in units, lots times the lot size', () => {
    // Two NIFTY lots are 130 units; a 1.5 point move is 195 rupees.
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 130,
      average_price: 80,
      ltp: 82,
      pnl: 260,
    }
    const priced = priceRow(row, { tick: tick(83.5, BOOK_AT + 1), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.pnl).toBeCloseTo(260 + 1.5 * 130)
  })

  it('ignores a tick older than the book: the broker figures stand', () => {
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 1950,
    }
    const priced = priceRow(row, { tick: tick(125, BOOK_AT - 1), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.ltp).toBe(130)
    expect(priced.pnl).toBe(1950)
    expect(priced.source).toBe('rest')
    expect(priced.at).toBe(BOOK_AT)
  })

  it('in analyze mode, moves the sandbox figure that already holds today_realized_pnl', () => {
    // The sandbox reports realised separately and in its pnl: 500 + (110 - 100) x 65.
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 65,
      average_price: 100,
      ltp: 110,
      pnl: 1150,
      today_realized_pnl: 500,
    }
    const priced = priceRow(row, { tick: tick(112, BOOK_AT + 1), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.pnl).toBeCloseTo(1280)
    // The same as the evaluator's own sandbox formula, so analyze mode is unchanged.
    expect(priced.pnl).toBeCloseTo(positionPnlAt(row, 112)?.pnl ?? Number.NaN)
  })

  it('keeps a closed row exactly as the broker reported it', () => {
    const row = { symbol: 'X', exchange: 'NFO', quantity: 0, average_price: 0, ltp: 98, pnl: -740 }
    const priced = priceRow(row, { tick: tick(90, BOOK_AT + 1), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced).toMatchObject({ ltp: 98, pnl: -740, source: 'rest', live: false })
  })

  it('prices a watchlist row, which has no quantity, from its newest source', () => {
    const row = { symbol: 'X', exchange: 'NSE' }
    const fromTick = priceRow(
      row,
      { tick: tick(1350, 2000), quoteLtp: 1300, quoteAt: 1000 },
      positionPnlAt
    )
    expect(fromTick.ltp).toBe(1350)
    const fromQuote = priceRow(
      row,
      { tick: tick(1350, 1000), quoteLtp: 1300, quoteAt: 2000 },
      positionPnlAt
    )
    expect(fromQuote).toMatchObject({ ltp: 1300, source: 'multiquotes', live: false })
  })

  it('takes the REST fallback the manager fills an outage with, but never calls it live', () => {
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 650,
    }
    const priced = priceRow(
      row,
      { tick: tick(131, BOOK_AT + 5000, 'rest'), bookAt: BOOK_AT },
      positionPnlAt
    )

    expect(priced.ltp).toBe(131)
    expect(priced.pnl).toBeCloseTo(650 + 65)
    expect(priced.live).toBe(false)
  })

  it('works out P&L % on what is still held', () => {
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 1950,
    }
    const priced = priceRow(row, { tick: tick(134, BOOK_AT + 1), bookAt: BOOK_AT }, positionPnlAt)

    expect(priced.pnlpercent).toBeCloseTo(((1950 + 260) / (120 * 65)) * 100)
  })
})

describe('TickDirections', () => {
  it('remembers which way the last tick moved, and asking again changes nothing', () => {
    const directions = new TickDirections()
    expect(directions.observe('NFO:X', 100)).toBeNull()
    expect(directions.observe('NFO:X', 101)).toBe('up')
    expect(directions.observe('NFO:X', 101)).toBe('up')
    expect(directions.observe('NFO:X', 99.5)).toBe('down')
    expect(directions.get('NFO:X')).toBe('down')
  })

  it('compares the first tick with the price shown before it', () => {
    const directions = new TickDirections()
    expect(directions.observe('NFO:X', 131, 130)).toBe('up')
    expect(new TickDirections().observe('NFO:Y', 129, 130)).toBe('down')
  })
})

describe('optional tick fields', () => {
  it('reads a previous close from prev_close or cp, never from close', () => {
    expect(tickPrevClose({ ltp: 101, prev_close: 99 } as never)).toBe(99)
    expect(tickPrevClose({ ltp: 101, cp: 98 } as never)).toBe(98)
    expect(tickPrevClose({ ltp: 101, close: 101 })).toBeNull()
  })

  it('reads a count, zero included, and nothing when it is not sent', () => {
    expect(tickCount({ ltp: 1, oi: 0 } as never, 'oi')).toBe(0)
    expect(tickCount({ ltp: 1, volume: 1250 }, 'volume')).toBe(1250)
    expect(tickCount({ ltp: 1 }, 'oi')).toBeNull()
  })
})
