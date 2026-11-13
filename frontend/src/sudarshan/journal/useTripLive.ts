/**
 * Open trades moving with ticks, one draw per frame (design 6.2).
 *
 * `useFrameLines` (LTP mode: a price, one draw per animation frame) brings the ticks of the open
 * trades' instruments; `livePnlPaise` prices each trade from its entry average; `useBookFeed` says
 * where the feed stands, so "Real-time" shows only while WebSocket ticks arrive and otherwise
 * Reconnecting, Stale, Waiting for prices or Market closed. Nothing here polls: the service's
 * trips are the first snapshot and a re-read the periodic re-sync.
 */
import { useMemo, useRef } from 'react'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { type FeedLine, keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import { useFrameLines } from '@/sudarshan/live/useFrameLines'
import { type JournalTick, livePnlPaise, openQty, tickOf } from './live'
import type { Trip } from './types'

/** The share of the one pooled connection the journal asks for. */
export const LIVE_CAP = 100

export interface LivePnl {
  /** The P&L at the last price; null until a price has come. */
  pnl_paise: number | null
  ltp_u: number | null
  /** True for a trade that should be moving and is not: its figure is shown greyed. */
  stale: boolean
  /** Whether a WebSocket tick brought the price. */
  live: boolean
}

export interface TripLive {
  pnl: ReadonlyMap<number, LivePnl>
  line: FeedLine
  now: number
}

/** The open trades that can be priced: those with a quantity left, the first `cap` of them. */
export function pricedTrips(trips: readonly Trip[], cap: number = LIVE_CAP): Trip[] {
  return trips.filter((t) => t.status === 'open' && openQty(t) > 0).slice(0, Math.max(0, cap))
}

export function useTripLive({
  trips,
  marketOpen,
  enabled = true,
}: {
  trips: readonly Trip[]
  marketOpen?: MarketOpen
  enabled?: boolean
}): TripLive {
  const priced = useMemo(() => pricedTrips(trips), [trips])
  const instruments = useMemo(() => {
    const seen = new Map<string, { symbol: string; exchange: string }>()
    for (const t of priced)
      seen.set(keyOf(t.symbol, t.exchange), { symbol: t.symbol, exchange: t.exchange })
    return [...seen.values()]
  }, [priced])
  const { lines, feed } = useFrameLines({ instruments, mode: 'LTP', enabled })

  // The newest good tick of each instrument: a price that is no price leaves the last one.
  const good = useRef(new Map<string, JournalTick>())
  const ticks = useMemo(() => {
    const wanted = new Set(instruments.map((i) => keyOf(i.symbol, i.exchange)))
    for (const key of [...good.current.keys()]) if (!wanted.has(key)) good.current.delete(key)
    for (const [key, line] of lines) {
      const tick = tickOf(line)
      if (tick) good.current.set(key, tick)
    }
    return new Map(good.current)
  }, [lines, instruments])

  const feedRows: FeedRow[] = useMemo(
    () =>
      priced.map((t) => {
        const tick = ticks.get(keyOf(t.symbol, t.exchange))
        return {
          exchange: t.exchange,
          ltp: tick ? tick.ltpU / 10_000 : undefined,
          _at: tick?.at ?? null,
          _live: tick?.live === true,
        }
      }),
    [priced, ticks]
  )
  const stamps = [...ticks.values()]
  const lastTickAt = stamps.reduce<number | null>(
    (best, t) => (t.live && (best === null || t.at > best) ? t.at : best),
    null
  )
  const lastUpdateAt = stamps.reduce<number | null>(
    (best, t) => (best === null || t.at > best ? t.at : best),
    null
  )
  const book = useBookFeed(
    feedRows,
    { lastTickAt, lastUpdateAt, due: priced.length, connected: feed.connected },
    marketOpen
  )

  // The figures move only with ticks; whether one is stale also moves with the clock.
  const priced$ = useMemo(
    () =>
      priced.map((t) => {
        const tick = ticks.get(keyOf(t.symbol, t.exchange))
        return {
          id: t.id,
          pnl_paise: tick ? livePnlPaise(t.side, openQty(t), t.entry_avg_u, tick.ltpU) : null,
          ltp_u: tick?.ltpU ?? null,
          live: tick?.live === true,
        }
      }),
    [priced, ticks]
  )
  const pnl = new Map<number, LivePnl>()
  priced$.forEach((figure, n) => {
    pnl.set(figure.id, {
      pnl_paise: figure.pnl_paise,
      ltp_u: figure.ltp_u,
      live: figure.live,
      stale: book.stale(feedRows[n]),
    })
  })

  return { pnl, line: book.line, now: book.now }
}
