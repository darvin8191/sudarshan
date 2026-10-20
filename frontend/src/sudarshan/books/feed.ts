import { MarketDataManager } from '@/lib/MarketDataManager'
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
 * The newest tick the market-data socket has delivered for any instrument in
 * this session, or null when it is down or has delivered none. A table's own
 * instruments can all be quiet while the feed is fine: an option nobody has
 * traded for a minute is not late, so it is not greyed while ticks arrive.
 */
export function newestTickAt(): number | null {
  try {
    const manager = MarketDataManager.getInstance()
    const { isAuthenticated, connectionEpoch } = manager.getState()
    if (!isAuthenticated) return null
    let newest: number | null = null
    for (const line of manager.getAllCachedData().values()) {
      if (line.updateSource !== 'websocket' || line.connectionEpoch !== connectionEpoch) continue
      if (typeof line.lastUpdate === 'number' && (newest === null || line.lastUpdate > newest)) {
        newest = line.lastUpdate
      }
    }
    return newest
  } catch {
    return null
  }
}

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
  // The feed is alive while any instrument ticks, not only this table's.
  const feedTickAt = latest(feed.lastTickAt, feed.connected ? newestTickAt() : null)
  const stale = (row: FeedRow) => {
    if (row.quantity !== undefined && Number(row.quantity) === 0) return false
    const ltp = typeof row.ltp === 'number' && row.ltp > 0 ? row.ltp : null
    return isStale(
      { ltp, prevClose: null, at: row._at ?? null, live: row._live === true },
      now,
      marketOpen(row.exchange),
      feedTickAt
    )
  }
  return { line, now, stale }
}
