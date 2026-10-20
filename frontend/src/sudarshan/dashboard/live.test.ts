/**
 * The dashboard's live arithmetic. Every case is the broker's figure as the
 * base plus what the ticks have moved it by since the position book was read,
 * so each test states the book, the ticks, and the move it expects.
 */
import { describe, expect, it } from 'vitest'
import { positionPnlAt } from '@/hooks/useLivePrice'
import type { SymbolData } from '@/lib/MarketDataManager'
import type { Position } from '@/types/trading'
import {
  ago,
  anyOpen,
  feedLine,
  type IndexView,
  indexView,
  isStale,
  istDay,
  keyOf,
  latest,
  liveMove,
  openPositions,
  priceOf,
  STALE_MS,
} from './live'

/** When the position book was asked for. */
const READ = 1_760_000_000_000

const CALL = 'NIFTY28OCT2525900CE'
const PUT = 'BANKNIFTY28OCT2557000PE'

function position(overrides: Partial<Position> = {}): Position {
  return {
    symbol: CALL,
    exchange: 'NFO',
    product: 'NRML',
    quantity: 65,
    average_price: 120,
    ltp: 130,
    pnl: 650,
    pnlpercent: 8.33,
    ...overrides,
  }
}

function prices(
  ...ticks: Array<{
    symbol?: string
    exchange?: string
    ltp: number
    at: number
    source?: 'websocket' | 'rest'
  }>
): Map<string, SymbolData> {
  const map = new Map<string, SymbolData>()
  for (const { symbol = CALL, exchange = 'NFO', ltp, at, source = 'websocket' } of ticks) {
    map.set(keyOf(symbol, exchange), {
      symbol,
      exchange,
      data: { ltp },
      lastUpdate: at,
      updateSource: source,
    })
  }
  return map
}

describe('liveMove', () => {
  it('moves a long by quantity times the rise since the book was read', () => {
    const move = liveMove(
      { positions: [position()], at: READ },
      prices({ ltp: 134, at: READ + 1000 })
    )
    expect(move.delta).toBeCloseTo(65 * 4, 6)
    expect(move.open).toBe(1)
    expect(move.lastTickAt).toBe(READ + 1000)
  })

  it('moves a short the other way: a rise is a loss', () => {
    const move = liveMove(
      { positions: [position({ quantity: -65, ltp: 130 })], at: READ },
      prices({ ltp: 134, at: READ + 1000 })
    )
    expect(move.delta).toBeCloseTo(-65 * 4, 6)
  })

  it('counts an F&O position in units, as the broker reports it: two NIFTY lots are 130', () => {
    const move = liveMove(
      {
        positions: [
          position({ quantity: 130 }),
          position({ symbol: PUT, quantity: -30, average_price: 410, ltp: 400 }),
        ],
        at: READ,
      },
      prices({ ltp: 131.5, at: READ + 500 }, { symbol: PUT, ltp: 396, at: READ + 700 })
    )
    // Long 130 up 1.50, short 30 (one BANKNIFTY lot) down 4.00: both gain.
    expect(move.delta).toBeCloseTo(130 * 1.5 + 30 * 4, 6)
    expect(move.lastTickAt).toBe(READ + 700)
  })

  it("applies the broker's contract multiplier where it sends one", () => {
    const move = liveMove(
      {
        positions: [
          position({
            symbol: 'ETHUSD.P',
            exchange: 'CRYPTO',
            quantity: 5,
            average_price: 3000,
            ltp: 3010,
            lot_size: 0.01,
          }),
        ],
        at: READ,
      },
      prices({ symbol: 'ETHUSD.P', exchange: 'CRYPTO', ltp: 3030, at: READ + 1000 })
    )
    expect(move.delta).toBeCloseTo(5 * 0.01 * 20, 9)
  })

  it('after a partial exit moves only what is still held, and leaves the realised part alone', () => {
    // Bought 130, sold 65: the book now holds 65, and analyze mode carries the
    // booked profit on the position. The funds call already counts it as realised.
    const move = liveMove(
      { positions: [position({ quantity: 65, today_realized_pnl: 500 })], at: READ },
      prices({ ltp: 132, at: READ + 1000 })
    )
    expect(move.delta).toBeCloseTo(65 * 2, 6)
  })

  it('does nothing when flat: closed positions do not move and are not counted as open', () => {
    const book = { positions: [position({ quantity: 0, average_price: 0, ltp: 128 })], at: READ }
    const move = liveMove(book, prices({ ltp: 140, at: READ + 1000 }))
    expect(move).toEqual({ delta: 0, open: 0, lastTickAt: null })
    expect(openPositions(book.positions)).toEqual([])
  })

  it('ignores a price that is not newer than the book, which already counts it', () => {
    const book = { positions: [position()], at: READ }
    expect(liveMove(book, prices({ ltp: 150, at: READ })).delta).toBe(0)
    const older = liveMove(book, prices({ ltp: 150, at: READ - 2000 }))
    expect(older.delta).toBe(0)
    // It is still a tick, so it still says when the feed last spoke.
    expect(older.lastTickAt).toBe(READ - 2000)
  })

  it('moves on a REST fallback price but does not count it as a tick', () => {
    const move = liveMove(
      { positions: [position()], at: READ },
      prices({ ltp: 131, at: READ + 1000, source: 'rest' })
    )
    expect(move.delta).toBeCloseTo(65, 6)
    expect(move.lastTickAt).toBeNull()
  })

  it('leaves a position it cannot price to the next read, as the Positions page does', () => {
    const tick = prices({ ltp: 140, at: READ + 1000 })
    expect(liveMove({ positions: [position({ average_price: 0 })], at: READ }, tick).delta).toBe(0)
    expect(liveMove({ positions: [position({ ltp: 0 })], at: READ }, tick).delta).toBe(0)
    expect(liveMove(null, tick)).toEqual({ delta: 0, open: 0, lastTickAt: null })
  })
})

describe('positionPnlAt, the evaluator the Positions page and the dashboard share', () => {
  it('prices a long, a short and a multiplier', () => {
    expect(positionPnlAt(position(), 130)?.pnl).toBeCloseTo(650, 6)
    expect(positionPnlAt(position({ quantity: -65 }), 130)?.pnl).toBeCloseTo(-650, 6)
    expect(
      positionPnlAt(position({ quantity: 5, average_price: 3000, lot_size: 0.01 }), 3010)?.pnl
    ).toBeCloseTo(0.5, 9)
  })

  it("adds today's realised P&L where the position carries it, and percent on the outlay", () => {
    const result = positionPnlAt(position({ today_realized_pnl: 500 }), 130)
    expect(result?.pnl).toBeCloseTo(1150, 6)
    expect(result?.pnlPercent).toBeCloseTo((1150 / (120 * 65)) * 100, 6)
  })

  it('cannot price without a price or an average price', () => {
    expect(positionPnlAt(position(), undefined)).toBeNull()
    expect(positionPnlAt(position(), 0)).toBeNull()
    expect(positionPnlAt(position({ average_price: 0 }), 130)).toBeNull()
  })
})

describe('priceOf', () => {
  it('says whether the WebSocket or a REST poll wrote the line', () => {
    const map = prices({ ltp: 10, at: READ }, { symbol: 'X', ltp: 11, at: READ, source: 'rest' })
    expect(priceOf(map.get(keyOf(CALL, 'NFO')))).toEqual({ ltp: 10, at: READ, live: true })
    expect(priceOf(map.get(keyOf('X', 'NFO')))).toEqual({ ltp: 11, at: READ, live: false })
  })

  it('holds nothing worth showing without a positive price or a time', () => {
    expect(priceOf(undefined)).toBeNull()
    expect(
      priceOf({ symbol: CALL, exchange: 'NFO', data: { ltp: 0 }, lastUpdate: READ })
    ).toBeNull()
    expect(priceOf({ symbol: CALL, exchange: 'NFO', data: { ltp: 5 } })).toBeNull()
  })
})

describe('indexView', () => {
  const quote = { ltp: 25874.3, prev_close: 25714.85 }

  it('takes the level from a tick newer than the read, and the previous close from the read', () => {
    const view = indexView(
      quote,
      READ,
      prices({ symbol: 'NIFTY', exchange: 'NSE_INDEX', ltp: 25880, at: READ + 50 }).get(
        'NSE_INDEX:NIFTY'
      )
    )
    expect(view).toEqual({ ltp: 25880, prevClose: 25714.85, at: READ + 50, live: true })
  })

  it('keeps the read when the only tick is older than it', () => {
    const view = indexView(
      quote,
      READ,
      prices({ symbol: 'NIFTY', exchange: 'NSE_INDEX', ltp: 25000, at: READ - 50 }).get(
        'NSE_INDEX:NIFTY'
      )
    )
    expect(view).toEqual({ ltp: 25874.3, prevClose: 25714.85, at: READ, live: false })
  })

  it('shows a tick with no change when there is no previous close, and nothing when there is nothing', () => {
    const tick = prices({ symbol: 'NIFTY', exchange: 'NSE_INDEX', ltp: 25880, at: READ }).get(
      'NSE_INDEX:NIFTY'
    )
    expect(indexView(undefined, null, tick)).toEqual({
      ltp: 25880,
      prevClose: null,
      at: READ,
      live: true,
    })
    expect(indexView({ ltp: 25874.3, prev_close: 0 }, READ, undefined).prevClose).toBeNull()
    expect(indexView(undefined, null, undefined)).toEqual({
      ltp: null,
      prevClose: null,
      at: null,
      live: false,
    })
  })
})

describe('isStale', () => {
  const ticked: IndexView = { ltp: 11.4, prevClose: 11.8, at: READ, live: true }
  const read: IndexView = { ...ticked, live: false }

  it('never while the market is closed or not known to be open', () => {
    expect(isStale(read, READ + 60_000, false, null)).toBe(false)
    expect(isStale(read, READ + 60_000, null, null)).toBe(false)
  })

  it('not while the level is under ten seconds old', () => {
    expect(isStale(read, READ + STALE_MS, true, null)).toBe(false)
  })

  it('a level only the REST read gave, once it is over ten seconds old', () => {
    expect(isStale(read, READ + STALE_MS + 1, true, READ + STALE_MS)).toBe(true)
  })

  it('not a ticked level while the rest of the strip is still ticking: a quiet index has not moved', () => {
    expect(isStale(ticked, READ + 30_000, true, READ + 29_000)).toBe(false)
  })

  it('every level once the whole feed has been quiet for ten seconds', () => {
    expect(isStale(ticked, READ + 30_000, true, READ + 19_000)).toBe(true)
    expect(isStale(ticked, READ + 30_000, true, null)).toBe(true)
  })

  it('not a level that is not there', () => {
    expect(isStale({ ...read, ltp: null }, READ + 60_000, true, null)).toBe(false)
  })
})

describe('feedLine', () => {
  it('says "Live" only while ticks are arriving, and stale once they stop in market hours', () => {
    expect(
      feedLine({ now: READ + STALE_MS, marketOpen: true, lastTickAt: READ, lastUpdateAt: READ })
    ).toEqual({ kind: 'live' })
    expect(
      feedLine({ now: READ + STALE_MS + 1, marketOpen: true, lastTickAt: READ, lastUpdateAt: READ })
    ).toEqual({ kind: 'delayed', at: READ, stale: true })
  })

  it('never "Live" on a REST answer alone, however fresh', () => {
    expect(
      feedLine({ now: READ + 3000, marketOpen: true, lastTickAt: null, lastUpdateAt: READ })
    ).toEqual({ kind: 'delayed', at: READ, stale: false })
  })

  it('calls nothing stale when the market is not known to be open, or nothing should tick', () => {
    expect(
      feedLine({ now: READ + 60_000, marketOpen: null, lastTickAt: null, lastUpdateAt: READ })
    ).toEqual({ kind: 'delayed', at: READ, stale: false })
    expect(
      feedLine({
        now: READ + 60_000,
        marketOpen: true,
        lastTickAt: null,
        lastUpdateAt: READ,
        ticksDue: false,
      })
    ).toEqual({ kind: 'delayed', at: READ, stale: false })
  })

  it('says it is reconnecting while the socket is down, however recent the last tick', () => {
    expect(
      feedLine({
        now: READ + 2000,
        marketOpen: true,
        lastTickAt: READ,
        lastUpdateAt: READ,
        connected: false,
      })
    ).toEqual({ kind: 'connecting', at: READ, stale: false, again: true })
    // After the phone wakes: the figures are old, and greyed.
    expect(
      feedLine({
        now: READ + 300_000,
        marketOpen: true,
        lastTickAt: READ,
        lastUpdateAt: READ,
        connected: false,
      })
    ).toEqual({ kind: 'connecting', at: READ, stale: true, again: true })
  })

  it('says connecting, not reconnecting, before the first tick', () => {
    expect(
      feedLine({
        now: READ,
        marketOpen: null,
        lastTickAt: null,
        lastUpdateAt: READ,
        connected: false,
      })
    ).toEqual({ kind: 'connecting', at: READ, stale: false, again: false })
  })

  it('does not talk about the socket when the market is closed or nothing is held', () => {
    expect(
      feedLine({
        now: READ,
        marketOpen: false,
        lastTickAt: null,
        lastUpdateAt: READ,
        connected: false,
      })
    ).toEqual({ kind: 'closed' })
    expect(
      feedLine({
        now: READ + 1000,
        marketOpen: true,
        lastTickAt: null,
        lastUpdateAt: READ,
        connected: false,
        ticksDue: false,
      })
    ).toEqual({ kind: 'delayed', at: READ, stale: false })
  })

  it('says the market is closed when it is known to be, and waits when there is nothing yet', () => {
    expect(
      feedLine({ now: READ, marketOpen: false, lastTickAt: READ, lastUpdateAt: READ })
    ).toEqual({ kind: 'closed' })
    expect(feedLine({ now: READ, marketOpen: null, lastTickAt: null, lastUpdateAt: null })).toEqual(
      { kind: 'waiting' }
    )
  })
})

describe('helpers', () => {
  it('anyOpen: open if one is, closed only if all are known closed', () => {
    expect(anyOpen([false, true])).toBe(true)
    expect(anyOpen([false, false])).toBe(false)
    expect(anyOpen([false, null])).toBeNull()
    expect(anyOpen([])).toBeNull()
  })

  it('istDay: the date in India, which turns at 18:30 UTC', () => {
    expect(istDay(Date.UTC(2026, 9, 20, 18, 29))).toBe('2026-10-20')
    expect(istDay(Date.UTC(2026, 9, 20, 18, 30))).toBe('2026-10-21')
  })

  it('latest ignores what is not known', () => {
    expect(latest(null, 5, undefined, 3)).toBe(5)
    expect(latest(null, undefined)).toBeNull()
  })

  it('ago reads in seconds, then minutes, then hours', () => {
    expect(ago(-20)).toBe('0s ago')
    expect(ago(12_400)).toBe('12s ago')
    expect(ago(59_000)).toBe('59s ago')
    expect(ago(90_000)).toBe('2m ago')
    expect(ago(2 * 3_600_000)).toBe('2h ago')
  })
})
