/**
 * Ticks for the rows of the Upstox tab, one draw per frame (M7.3). `useFrameLines` brings the
 * ticks (LTP mode) of up to LIVE_CAP held instruments; `useBookFeed` says where the feed stands
 * so "Real-time" shows only while ticks arrive. Nothing polls: the service's rows are the first
 * snapshot, `holdingNow` and `positionNow` move them, and a re-read is only a re-sync.
 */
import { useMemo, useRef } from 'react'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import { useFrameLines } from '@/sudarshan/live/useFrameLines'
import { LIVE_CAP, type Tick, tickOf } from '../liveTab'
import type { UpstoxHolding, UpstoxPosition } from '../types'

type Listed = Pick<UpstoxHolding, 'exchange' | 'symbol'>

export const instrumentOf = (row: Listed): string | null =>
  row.exchange && row.symbol ? keyOf(row.symbol, row.exchange) : null

export interface UpstoxLive {
  ticks: ReadonlyMap<string, Tick>
  /** The direction of each instrument's last tick, once it has moved. */
  directions: ReadonlyMap<string, 'up' | 'down'>
  line: ReturnType<typeof useBookFeed>['line']
  now: number
  stale: (row: Listed & { held: boolean }) => boolean
}

export function useUpstoxLive({
  holdings,
  positions,
  marketOpen,
  enabled = true,
}: {
  holdings: readonly UpstoxHolding[]
  positions: readonly UpstoxPosition[]
  marketOpen?: MarketOpen
  enabled?: boolean
}): UpstoxLive {
  // Held rows only, each instrument once, the first LIVE_CAP of them.
  const wanted = useMemo(() => {
    const seen = new Map<string, { symbol: string; exchange: string }>()
    for (const row of [...holdings, ...positions.filter((p) => p.qty_u !== 0)]) {
      const key = instrumentOf(row)
      if (key && !seen.has(key)) {
        seen.set(key, { symbol: row.symbol as string, exchange: row.exchange as string })
      }
    }
    return [...seen.values()].slice(0, LIVE_CAP)
  }, [holdings, positions])
  const { lines, directions, feed } = useFrameLines({
    instruments: wanted,
    mode: 'LTP',
    enabled: enabled && wanted.length > 0,
  })

  // The newest good tick of each instrument: a price that is no price leaves the last one.
  const good = useRef(new Map<string, Tick>())
  const ticks = useMemo(() => {
    const keys = new Set(wanted.map((i) => keyOf(i.symbol, i.exchange)))
    for (const key of [...good.current.keys()]) if (!keys.has(key)) good.current.delete(key)
    for (const [key, line] of lines) {
      const tick = tickOf(line)
      if (tick) good.current.set(key, tick)
    }
    return new Map(good.current)
  }, [lines, wanted])

  const feedRows: FeedRow[] = useMemo(
    () =>
      wanted.map((i) => {
        const tick = ticks.get(keyOf(i.symbol, i.exchange))
        return {
          exchange: i.exchange,
          ltp: tick?.ltp,
          _at: tick?.at ?? null,
          _live: tick?.live === true,
        }
      }),
    [wanted, ticks]
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
    { lastTickAt, lastUpdateAt, due: feedRows.length, connected: feed.connected },
    marketOpen
  )
  const stale = (row: Listed & { held: boolean }): boolean => {
    const key = instrumentOf(row)
    if (!key || !row.held) return false
    const tick = ticks.get(key)
    return book.stale({
      exchange: row.exchange ?? 'NSE',
      ltp: tick?.ltp,
      _at: tick?.at ?? null,
      _live: tick?.live === true,
    })
  }
  return { ticks, directions, line: book.line, now: book.now, stale }
}
