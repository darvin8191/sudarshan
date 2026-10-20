import { useNow } from '@/sudarshan/dashboard/hooks'
import {
  anyOpen,
  type FeedLine,
  feedLine,
  isStale,
  latest,
  type MarketOpen,
} from '@/sudarshan/dashboard/live'

/**
 * Where a live table's prices stand (M6.2): the status line above it and
 * which of its rows to grey. The same rules as the dashboard's P&L card
 * (src/sudarshan/dashboard/live.ts): "Real-time" only while WebSocket ticks
 * arrive; otherwise "Connecting" or "Reconnecting" while the market-data
 * socket is down, the age of the figures, "Stale" once ticks are due and have
 * stopped for 10 seconds, or "Market closed".
 */

/** What a table knows about its feed (useLivePrice's `feed`, or a page's own). */
export interface FeedSummary {
  lastTickAt: number | null
  lastUpdateAt: number | null
  /** Rows whose prices should be ticking. */
  due: number
  connected: boolean
}

/** A row as the stale rule reads it. */
export interface FeedRow {
  exchange: string
  ltp?: number
  quantity?: number
  _at?: number | null
  _live?: boolean
}

export interface BookFeed {
  line: FeedLine
  now: number
  /** True for a row whose price should be moving and is not: shown greyed, sign and arrow kept. */
  stale: (row: FeedRow) => boolean
}

const NOT_KNOWN: MarketOpen = () => null

/**
 * The status line for a table, and its stale test for one row. Re-renders the
 * caller once a second while the page is in view, so an age or a stale mark
 * is current even when no tick comes to prompt it.
 */
export function useBookFeed(
  rows: readonly FeedRow[],
  feed: FeedSummary,
  marketOpen: MarketOpen = NOT_KNOWN
): BookFeed {
  const now = useNow()
  const due = rows.filter((row) => row.quantity === undefined || Number(row.quantity) !== 0)
  const open =
    due.length > 0 ? anyOpen(due.map((row) => marketOpen(row.exchange))) : marketOpen('NSE')
  const line = feedLine({
    now,
    marketOpen: open,
    lastTickAt: feed.lastTickAt,
    lastUpdateAt: latest(feed.lastTickAt, feed.lastUpdateAt),
    ticksDue: feed.due > 0,
    connected: feed.connected,
  })
  const stale = (row: FeedRow) => {
    if (row.quantity !== undefined && Number(row.quantity) === 0) return false
    const ltp = typeof row.ltp === 'number' && row.ltp > 0 ? row.ltp : null
    return isStale(
      { ltp, prevClose: null, at: row._at ?? null, live: row._live === true },
      now,
      marketOpen(row.exchange),
      feed.lastTickAt
    )
  }
  return { line, now, stale }
}
