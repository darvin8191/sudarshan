import { useEffect, useMemo, useRef } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { useMarketData } from '@/hooks/useMarketData'
import type { SymbolData } from '@/lib/MarketDataManager'
import { useNow } from '@/sudarshan/dashboard/hooks'
import {
  anyOpen,
  type FeedLine,
  feedLine,
  isStale,
  keyOf,
  latest,
  type MarketOpen,
  priceOf,
} from '@/sudarshan/dashboard/live'
import { type RowView, rowView, type TickDirection, tickDirection } from './rows'
import { type SnapshotStatus, useQuoteSnapshot } from './useQuoteSnapshot'

/** One instrument of the open list, as it stands this second. */
export interface LiveRow {
  item: WatchlistItem
  /** EXCHANGE:SYMBOL, the market-data cache key. */
  key: string
  view: RowView
  /** The way the last tick moved the level shown, once it has moved. */
  direction: TickDirection | null
  /** Ticks are due and this level has stopped arriving. */
  stale: boolean
}

/**
 * The newest cache line seen for each instrument, kept across a
 * re-subscription. Adding a symbol re-subscribes the whole list, and the
 * market-data manager empties a line when its last subscriber leaves, so
 * without this every row would drop back to the snapshot for a moment.
 */
function useNewestLines(prices: Map<string, SymbolData>): ReadonlyMap<string, SymbolData> {
  const kept = useRef(new Map<string, SymbolData>())
  return useMemo(() => {
    for (const [key, line] of prices) {
      if (line.lastUpdate === undefined) continue
      const held = kept.current.get(key)
      if (!held || (held.lastUpdate ?? 0) <= line.lastUpdate) kept.current.set(key, line)
    }
    return new Map(kept.current)
  }, [prices])
}

/**
 * The direction of each row's last move, from the level shown in the last
 * committed render to the level in this one. Kept per instrument, so it
 * survives switching lists and back.
 */
function useTickDirections(
  levels: Array<{ key: string; ltp: number | null }>
): ReadonlyMap<string, TickDirection> {
  const history = useRef(new Map<string, { ltp: number | null; direction: TickDirection | null }>())
  const directions = new Map<string, TickDirection>()
  for (const { key, ltp } of levels) {
    const held = history.current.get(key)
    const direction = tickDirection(held?.ltp ?? null, ltp, held?.direction ?? null)
    if (direction) directions.set(key, direction)
  }
  useEffect(() => {
    for (const { key, ltp } of levels) {
      const held = history.current.get(key)
      history.current.set(key, {
        ltp: ltp ?? held?.ltp ?? null,
        direction: tickDirection(held?.ltp ?? null, ltp, held?.direction ?? null),
      })
    }
  })
  return directions
}

/**
 * The open list, tick by tick (M8.2): every instrument streams over the tab's
 * one shared market-data socket (`useMarketData`, LTP mode, ref-counted by
 * `MarketDataManager`), starting from one REST snapshot that also gives the
 * previous close. Only the open list is subscribed: switching lists or leaving
 * the page closes the subscriptions it opened.
 */
export function useLiveRows({
  items,
  apiKey,
  marketOpen,
  anyMarketOpen,
}: {
  items: WatchlistItem[]
  apiKey: string | null
  marketOpen: MarketOpen
  /** Whether any main exchange is open: the snapshot reads again when this changes. */
  anyMarketOpen: boolean | null
}): {
  rows: LiveRow[]
  /** What the line above the list says; null for an empty list. */
  line: FeedLine | null
  now: number
  /** The newest WebSocket tick in the list, for the sheet's own stale marks. */
  feedTickAt: number | null
  snapshot: SnapshotStatus
  retry: () => void
} {
  const itemsKey = items.map((item) => keyOf(item.symbol, item.exchange)).join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: itemsKey is the identity of the instruments; the array is new on every read of the lists
  const instruments = useMemo(
    () => items.map(({ symbol, exchange }) => ({ symbol, exchange })),
    [itemsKey]
  )

  const {
    data: prices,
    connectionEpoch,
    isAuthenticated,
    isPaused,
    isFallbackMode,
  } = useMarketData({ symbols: instruments, mode: 'LTP', enabled: apiKey !== null })
  const snapshot = useQuoteSnapshot({
    apiKey,
    instruments,
    connectionEpoch,
    marketOpen: anyMarketOpen,
  })
  const lines = useNewestLines(prices)
  const now = useNow()

  const views = items.map((item) => {
    const key = keyOf(item.symbol, item.exchange)
    const line = lines.get(key)
    const tick = priceOf(line)
    return {
      item,
      key,
      view: rowView(snapshot.quotes.get(key), line),
      tickAt: tick?.live ? tick.at : null,
    }
  })
  const directions = useTickDirections(views.map(({ key, view }) => ({ key, ltp: view.ltp })))
  const feedTickAt = latest(...views.map((row) => row.tickAt))

  const rows = views.map(({ item, key, view }) => ({
    item,
    key,
    view,
    direction: directions.get(key) ?? null,
    stale: isStale(view, now, marketOpen(item.exchange), feedTickAt),
  }))

  let line: FeedLine | null = null
  if (items.length > 0) {
    const exchanges = [...new Set(items.map((item) => item.exchange))]
    line = feedLine({
      now,
      marketOpen: anyOpen(exchanges.map((exchange) => marketOpen(exchange))),
      lastTickAt: feedTickAt,
      lastUpdateAt: latest(...views.map(({ view }) => view.at)),
      connected: isAuthenticated && !isPaused,
    })
    // While the socket is down, upstream's REST fallback may be moving the
    // figures. They are not ticks, so the line says "Reconnecting" with their
    // age even before the first tick of the visit, never "Connecting" alone.
    if (line.kind === 'connecting' && !line.again && isFallbackMode && line.at !== null) {
      line = { ...line, again: true }
    }
  }

  return {
    rows,
    line,
    now,
    feedTickAt,
    snapshot: snapshot.status,
    retry: snapshot.retry,
  }
}
