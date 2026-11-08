/**
 * The instruments of a screen's results that the page follows tick by tick (design 4.1, cross-design
 * R4): the first 100, in rank order, as list items the watchlist's live rows already know how to
 * follow. Their ids stand for rank only; they are never sent anywhere.
 */
import type { WatchlistItem } from '@/api/watchlist'
import { keyOf } from '@/sudarshan/dashboard/live'
import type { ResultRow } from './types'

/** Instruments the results page follows on OpenAlgo's one pooled connection (cross-design R4). */
export const LIVE_CAP = 100

export function followedItems(rows: readonly ResultRow[], cap = LIVE_CAP): WatchlistItem[] {
  const seen = new Set<string>()
  const items: WatchlistItem[] = []
  for (const row of rows) {
    const key = keyOf(row.symbol, row.exchange)
    if (seen.has(key)) continue
    seen.add(key)
    if (items.length >= cap) break
    items.push({
      id: items.length + 1,
      symbol: row.symbol,
      exchange: row.exchange,
      position: items.length,
    })
  }
  return items
}

/** Rows past the cap, which keep the price of the run and say so. */
export function unfollowed(rows: readonly ResultRow[], cap = LIVE_CAP): ResultRow[] {
  const followed = new Set(
    followedItems(rows, cap).map((item) => keyOf(item.symbol, item.exchange))
  )
  return rows.filter((row) => !followed.has(keyOf(row.symbol, row.exchange)))
}
