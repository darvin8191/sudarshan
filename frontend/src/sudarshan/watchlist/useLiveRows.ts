import { useEffect, useMemo, useRef } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { useNow } from '@/sudarshan/dashboard/hooks'
import {
  anyOpen,
  type FeedLine,
  keyOf,
  latest,
  type MarketOpen,
  priceOf,
} from '@/sudarshan/dashboard/live'
import { type FeedState, useFrameLines } from '@/sudarshan/live/useFrameLines'
import {
  QUIET_RESYNC_MS,
  type RowView,
  rowStale,
  rowView,
  statusLine,
  type TickDirection,
} from './rows'
import { type SnapshotStatus, useQuoteSnapshot } from './useQuoteSnapshot'
import type { Basis } from './v2/types'

/** A quiet row whose quote was read this recently is left out of its re-sync. */
const QUIET_FRESH_MS = 5_000

/** The exchanges whose opening and closing read the snapshot again, the same for every list. */
const MAIN_EXCHANGES = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX', 'CDS']

/** One instrument of the open list, as it stands this frame. */
export interface LiveRow {
  item: WatchlistItem
  /** EXCHANGE:SYMBOL, the market-data cache key. */
  key: string
  view: RowView
  /** The direction of its last tick, once it has moved. */
  direction: TickDirection | null
  /** Its market is open, the socket is down and its level has stopped arriving. */
  stale: boolean
  /** When its last WebSocket tick came, or null when none has. */
  tickAt: number | null
}

/**
 * The open list, tick by tick (M8.2): every instrument streams over the tab's
 * one shared market-data socket in LTP mode (`useFrameLines`, drawn once per
 * frame), starting from one REST snapshot that also gives the previous close.
 * Only the open list is subscribed: an instrument that leaves it is released,
 * and leaving the page releases them all.
 *
 * The line above the list follows the socket: "Real-time" while ticks come,
 * "Reconnecting" with the figures' age while the socket is down ("Delayed"
 * while upstream's REST fallback fills in), and the age alone when the socket
 * is up but nothing in the list has traded for a while, which is not
 * staleness. Rows that have not ticked for a minute, their market
 * open and the socket up, are read again over REST, once a minute at most.
 */
export function useLiveRows({
  items,
  apiKey,
  marketOpen,
  basis = 'close', // F6
}: {
  items: WatchlistItem[]
  apiKey: string | null
  marketOpen: MarketOpen
  /** F6: what the change is measured from. */
  basis?: Basis
}): {
  rows: LiveRow[]
  /** What the line above the list says; null for an empty list. */
  line: FeedLine | null
  now: number
  feed: FeedState
  snapshot: SnapshotStatus
  /** F6: a REST read has landed, or failed: the prices a sort ranks on are there to be had. */
  read: boolean
  retry: () => void
} {
  const itemsKey = useMemo(
    () => items.map((item) => keyOf(item.symbol, item.exchange)).join(','),
    [items]
  )
  // biome-ignore lint/correctness/useExhaustiveDependencies: itemsKey is the identity of the instruments; the array is new on every read of the lists
  const instruments = useMemo(
    () => items.map(({ symbol, exchange }) => ({ symbol, exchange })),
    [itemsKey]
  )

  const { lines, directions, feed } = useFrameLines({
    instruments,
    mode: 'LTP',
    enabled: apiKey !== null,
  })
  const now = useNow()
  const snapshot = useQuoteSnapshot({
    apiKey,
    instruments,
    connectionEpoch: feed.epoch,
    marketOpen: anyOpen(MAIN_EXCHANGES.map((exchange) => marketOpen(exchange))),
  })

  // Whether each exchange is open, asked once a render rather than once a row.
  const opens = new Map<string, boolean | null>()
  const openOf = (exchange: string): boolean | null => {
    if (!opens.has(exchange)) opens.set(exchange, marketOpen(exchange))
    return opens.get(exchange) ?? null
  }

  const rows = items.map((item) => {
    const key = keyOf(item.symbol, item.exchange)
    const line = lines.get(key)
    const view = rowView(snapshot.quotes.get(key), line, basis) // F6
    const tick = priceOf(line)
    return {
      item,
      key,
      view,
      direction: directions.get(key) ?? null,
      stale: rowStale(view, now, openOf(item.exchange), feed.connected),
      tickAt: tick?.live ? tick.at : null,
    }
  })

  // Quiet rows: no tick for a minute while their market is open and the socket
  // is up. One REST read for them, at most once a minute, the first a minute
  // after the list opens; a quote read in the last few seconds (the phone
  // waking, a reconnect) is not read again.
  const quietKeys = feed.connected
    ? rows
        .filter(
          (row) =>
            openOf(row.item.exchange) === true &&
            (row.tickAt === null || now - row.tickAt > QUIET_RESYNC_MS)
        )
        .map((row) => row.key)
        .join(',')
    : ''
  const lastResync = useRef<number | null>(null)
  const resync = snapshot.resync
  useEffect(() => {
    if (lastResync.current === null) lastResync.current = now
    if (quietKeys === '' || apiKey === null || now - lastResync.current < QUIET_RESYNC_MS) return
    lastResync.current = now
    resync(new Set(quietKeys.split(',')), QUIET_FRESH_MS)
  }, [quietKeys, now, apiKey, resync])

  // F6: from today's open, a row whose open is not known gets one more read at its first tick
  // after the opening, once; the market opening resets it (the snapshot reads on that event itself).
  const noOpen =
    basis === 'open'
      ? rows
          .filter(
            (row) =>
              row.view.openMissing && row.tickAt !== null && openOf(row.item.exchange) === true
          )
          .map((row) => row.key)
          .join(',')
      : ''
  const asked = useRef({ open: false, keys: new Set<string>() })
  const isOpenNow = anyOpen(MAIN_EXCHANGES.map(openOf)) === true
  useEffect(() => {
    if (asked.current.open !== isOpenNow) asked.current = { open: isOpenNow, keys: new Set() }
    const keys = noOpen.split(',').filter((key) => key !== '' && !asked.current.keys.has(key))
    if (keys.length === 0 || apiKey === null) return
    for (const key of keys) asked.current.keys.add(key)
    resync(new Set(keys), 0)
  }, [noOpen, isOpenNow, apiKey, resync])

  let line: FeedLine | null = null
  if (items.length > 0) {
    const exchanges = [...new Set(items.map((item) => item.exchange))]
    line = statusLine({
      now,
      marketOpen: anyOpen(exchanges.map(openOf)),
      lastTickAt: latest(...rows.map((row) => row.tickAt)),
      lastUpdateAt: latest(...rows.map((row) => row.view.at)),
      connected: feed.connected,
      fallback: feed.fallback,
    })
  }

  return {
    rows,
    line,
    now,
    feed,
    snapshot: snapshot.status,
    read: snapshot.quotes.size > 0 || snapshot.status === 'error', // F6
    retry: snapshot.retry,
  }
}
