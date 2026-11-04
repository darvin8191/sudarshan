/**
 * A tab's figures moving with ticks, one draw per frame (M7.24).
 *
 * `useFrameLines` (LTP mode: a price, one draw per animation frame) brings the ticks of up to
 * `LIVE_CAP` held listed rows; `moveTab` prices the tab from the ones newer than the service's read
 * (liveTab.ts holds every rule); `useBookFeed` says where the feed stands, so "Real-time" shows only
 * while WebSocket ticks arrive and otherwise Reconnecting, Stale, Waiting or Market closed. Nothing
 * here polls; the service's rows are the first snapshot and a re-read the periodic re-sync.
 */
import { useMemo, useRef } from 'react'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { type FeedLine, keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import { useFrameLines } from '@/sudarshan/live/useFrameLines'
import {
  LIVE_CAP,
  type LiveRowInput,
  liveIndexes,
  liveInstruments,
  type MovedTab,
  moveTab,
  type Tick,
  tickOf,
} from './liveTab'
import type { ValuationFixed } from './valuation'

export interface TabLive<T extends LiveRowInput> extends MovedTab<T> {
  line: FeedLine
  now: number
  /** True for a row that should be moving and is not: shown greyed, sign and arrow kept. */
  stale: (row: T) => boolean
  /** The newest WebSocket tick of any of the tab's instruments, or null. */
  lastTickAt: number | null
}

export function useTabLive<T extends LiveRowInput>({
  rows,
  fixed,
  readAt,
  marketOpen,
  cap = LIVE_CAP,
  enabled = true,
}: {
  rows: readonly T[]
  fixed: ValuationFixed
  /** When the service read the tab: only ticks after it move a figure. */
  readAt: number
  marketOpen?: MarketOpen
  cap?: number
  enabled?: boolean
}): TabLive<T> {
  const instruments = useMemo(() => liveInstruments(rows, cap), [rows, cap])
  const { lines, feed } = useFrameLines({ instruments, mode: 'LTP', enabled })

  // The newest good tick of each instrument: a price that is no price leaves the last one.
  const good = useRef(new Map<string, Tick>())
  const ticks = useMemo(() => {
    const wanted = new Set(instruments.map((i) => keyOf(i.symbol, i.exchange)))
    for (const key of [...good.current.keys()]) if (!wanted.has(key)) good.current.delete(key)
    for (const [key, line] of lines) {
      const tick = tickOf(line)
      if (tick) good.current.set(key, tick)
    }
    return new Map(good.current)
  }, [lines, instruments])

  const moved = useMemo(
    () => moveTab(rows, fixed, ticks, readAt, cap),
    [rows, fixed, ticks, readAt, cap]
  )

  const feedRows: FeedRow[] = useMemo(
    () =>
      [...liveIndexes(rows, cap)].map((n) => {
        const row = rows[n]
        const tick = ticks.get(keyOf(row.asset.symbol as string, row.asset.exchange as string))
        return {
          exchange: row.asset.exchange as string,
          ltp: tick?.ltp,
          _at: tick?.at ?? null,
          _live: tick?.live === true,
        }
      }),
    [rows, ticks, cap]
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
  const staleOf = (row: T): boolean => {
    const key =
      row.asset.exchange && row.asset.symbol ? keyOf(row.asset.symbol, row.asset.exchange) : null
    const tick = key ? ticks.get(key) : undefined
    return book.stale({
      exchange: row.asset.exchange ?? 'NSE',
      ltp: tick?.ltp,
      _at: tick?.at ?? null,
      _live: tick?.live === true,
    })
  }
  return { ...moved, line: book.line, now: book.now, stale: staleOf, lastTickAt }
}
