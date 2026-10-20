/**
 * How a book row is priced (M6.3): while the feed is up, the instrument's last
 * tick from it wins (a later trade would have brought a later tick, and a book
 * or quote read since can trail the feed); otherwise the newest source does.
 * An open row's P&L is the broker's own figure moved by the price change since
 * the book was read. The defect this replaces: upstream rebuilt the P&L from the
 * average price plus `today_realized_pnl`, a field only the sandbox sends, so
 * a partly booked Upstox position lost its booked profit on the first tick.
 */
import { describe, expect, it } from 'vitest'
import { positionPnlAt } from '@/hooks/useLivePrice'
import type { SymbolData } from '@/lib/MarketDataManager'
import { isStale } from '@/sudarshan/dashboard/live'
import { priceRow, TickDirections, tickCount, tickPrevClose } from './prices'

const BOOK_AT = 1_000_000

function tick(
  ltp: number,
  at: number,
  source: 'websocket' | 'rest' = 'websocket',
  epoch?: number,
  extra: Record<string, number> = {}
): SymbolData {
  return {
    symbol: 'X',
    exchange: 'NFO',
    data: { ltp, ...extra },
    lastUpdate: at,
    updateSource: source,
    connectionEpoch: epoch,
  }
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

  it('ignores a tick older than the book when it is not from the live feed: the broker figures stand', () => {
    const row = {
      symbol: 'X',
      exchange: 'NFO',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 1950,
    }
    // The manager's REST fallback during an outage, and a tick from an earlier session of the feed.
    for (const old of [tick(125, BOOK_AT - 1, 'rest'), tick(125, BOOK_AT - 1, 'websocket', 1)]) {
      const priced = priceRow(row, { tick: old, bookAt: BOOK_AT, epoch: 2 }, positionPnlAt)
      expect(priced.ltp).toBe(130)
      expect(priced.pnl).toBe(1950)
      expect(priced.source).toBe('rest')
      expect(priced.at).toBe(BOOK_AT)
      expect(priced.live).toBe(false)
    }
    // While the feed is down, no tick is live, however recent.
    const down = priceRow(
      row,
      { tick: tick(125, BOOK_AT - 1, 'websocket', 2), bookAt: BOOK_AT, epoch: null },
      positionPnlAt
    )
    expect(down).toMatchObject({ ltp: 130, live: false })
  })

  it('keeps a quiet option at its last live tick after a re-read, and does not grey it while the feed is alive', () => {
    // Its last tick (134) came 300 ms before the book was asked for; the book's
    // own last price (130) trails the feed. Other instruments keep ticking.
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
      { tick: tick(134, BOOK_AT - 300, 'websocket', 3), bookAt: BOOK_AT, epoch: 3 },
      positionPnlAt
    )
    expect(priced).toMatchObject({ ltp: 134, live: true, source: 'websocket' })
    expect(priced.pnl).toBeCloseTo(650 + 4 * 65)
    const stale = isStale(
      { ltp: priced.ltp ?? null, prevClose: null, at: priced.at, live: priced.live },
      BOOK_AT + 11_000,
      true,
      BOOK_AT + 10_500
    )
    expect(stale).toBe(false)
  })

  it('takes a quote over the live tick only when both carry the exchange time and the quote saw a later trade', () => {
    const row = { symbol: 'X', exchange: 'NSE' }
    const live = tick(1350, 2000, 'websocket', 1, { last_trade_time: 5_000 })
    const later = priceRow(
      row,
      { tick: live, quoteLtp: 1352, quoteAt: 2500, quoteTradeAt: 6_000, epoch: 1 },
      positionPnlAt
    )
    expect(later).toMatchObject({ ltp: 1352, source: 'multiquotes', live: false })
    const earlier = priceRow(
      row,
      { tick: live, quoteLtp: 1352, quoteAt: 2500, quoteTradeAt: 4_000, epoch: 1 },
      positionPnlAt
    )
    expect(earlier).toMatchObject({ ltp: 1350, live: true })
    const unknown = priceRow(
      row,
      { tick: live, quoteLtp: 1352, quoteAt: 2500, epoch: 1 },
      positionPnlAt
    )
    expect(unknown.ltp).toBe(1350)
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

  it('prices a watchlist row, which has no quantity, from its live tick, else its newest source', () => {
    const row = { symbol: 'X', exchange: 'NSE' }
    const fromTick = priceRow(
      row,
      { tick: tick(1350, 1000), quoteLtp: 1300, quoteAt: 2000 },
      positionPnlAt
    )
    expect(fromTick).toMatchObject({ ltp: 1350, live: true })
    const fromQuote = priceRow(
      row,
      { tick: tick(1350, 1000, 'rest'), quoteLtp: 1300, quoteAt: 2000 },
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
