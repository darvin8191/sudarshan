import { useEffect, useMemo, useRef } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import type { SymbolData } from '@/lib/MarketDataManager'
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
import { type FollowRow, followPlan, instrumentOf, rowFollowed } from './follow'
import {
  QUIET_RESYNC_MS,
  type RowView,
  rowStale,
  rowView,
  type Snapshot,
  statusLine,
  type TickDirection,
} from './rows'
import { useFollowWindow } from './useFollowWindow'
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
  /**
   * F9: why this row is not followed, when it is not: past the cap, or outside the window of a long list.
   * It then shows the last figure it had, with the time of it, and is never re-read on a timer.
   */
  outside?: 'cap' | 'window'
}

/** F9 (v2): what the page follows besides its rows. Absent with the switch off: every row is followed, as it always was. */
export interface FollowOptions {
  /** The open sheet's instruments (EXCHANGE:SYMBOL). */
  sheet?: readonly string[]
  /** The underlying of each follow row on screen, once each (F11). */
  underlyings?: readonly string[]
  /** A strike row follows its CE and PE: its key, and the two (F12). */
  strikes?: ReadonlyMap<string, readonly string[]>
  /** F12: the keys followed in Quote mode (open interest): a strike row's two legs and the strike sheet's. */
  quote?: readonly string[]
  /** The rows in the order they are shown, as keys, when that is not the list's own (a sort). */
  order?: readonly string[]
  /** Changes when another list opens: the window starts at its top. */
  listKey?: string
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
  follow, // F9
}: {
  items: WatchlistItem[]
  apiKey: string | null
  marketOpen: MarketOpen
  /** F6: what the change is measured from. */
  basis?: Basis
  /** F9: the cap and the window apply; absent, every row is followed. */
  follow?: FollowOptions
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
  /** F9: read every quote again at once and give them; null when a call failed. */
  readAll: () => Promise<ReadonlyMap<string, Snapshot> | null>
  /** F11: the newest cache line of every followed instrument, for the follow rows' underlyings. */
  lines: ReadonlyMap<string, SymbolData>
  /** F12: the direction of each followed instrument's last tick (the strike sheet's other expiries). */
  directions: ReadonlyMap<string, TickDirection>
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

  // F9: what the socket follows. Without `follow` it is the whole list, as it was.
  const displayKeys = follow?.order && follow.order.length === items.length ? follow.order : null
  const itemKeys = itemsKey === '' ? [] : itemsKey.split(',')
  const shownKeys = displayKeys ?? itemKeys
  const start = useFollowWindow(shownKeys.length, follow?.listKey ?? '')
  const sheetKey = follow?.sheet?.join(',') ?? ''
  const underlyingKey = follow?.underlyings?.join(',') ?? ''
  const strikes = follow?.strikes
  const shownJoined = shownKeys.join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: the joined keys are the identity of the inputs; the arrays are new on every render
  const plan = useMemo(() => {
    if (!follow) return null
    const rows: FollowRow[] = shownKeys.map((key) => ({ key, keys: strikes?.get(key) }))
    return followPlan({
      sheet: sheetKey === '' ? [] : sheetKey.split(','),
      underlyings: underlyingKey === '' ? [] : underlyingKey.split(','),
      rows,
      start,
    })
  }, [follow !== undefined, shownJoined, sheetKey, underlyingKey, strikes, start])
  const followedKey = plan ? [...plan.keys].sort().join(',') : ''
  // biome-ignore lint/correctness/useExhaustiveDependencies: followedKey is the identity of the followed set
  const followed = useMemo(
    () => (plan ? [...plan.keys].map(instrumentOf) : instruments),
    [plan ? followedKey : instruments]
  )
  const reason: 'cap' | 'window' = plan?.window ? 'window' : 'cap'

  // F12: a strike row's legs and the strike sheet follow in Quote mode (they carry open interest), the
  // rest in LTP mode; an instrument is in exactly one of the two.
  const quoteJoined = follow?.quote?.join(',') ?? ''
  // biome-ignore lint/correctness/useExhaustiveDependencies: quoteJoined is the identity of the quote keys
  const split = useMemo(() => {
    const quoted = new Set(quoteJoined === '' ? [] : quoteJoined.split(','))
    if (quoted.size === 0) return { ltp: followed, quote: [] as typeof followed }
    const isQuoted = (each: { symbol: string; exchange: string }) =>
      quoted.has(keyOf(each.symbol, each.exchange))
    return { ltp: followed.filter((each) => !isQuoted(each)), quote: followed.filter(isQuoted) }
  }, [followed, quoteJoined])
  const main = useFrameLines({
    instruments: split.ltp,
    mode: 'LTP',
    enabled: apiKey !== null,
  })
  const quoted = useFrameLines({
    instruments: split.quote,
    mode: 'Quote',
    enabled: apiKey !== null,
  })
  const { feed } = main
  const lines = useMemo(
    () => (split.quote.length === 0 ? main.lines : new Map([...main.lines, ...quoted.lines])),
    [split.quote.length, main.lines, quoted.lines]
  )
  const directions = useMemo(
    () =>
      split.quote.length === 0
        ? main.directions
        : new Map([...main.directions, ...quoted.directions]),
    [split.quote.length, main.directions, quoted.directions]
  )
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

  // F9: the last WebSocket tick of each row of the list, kept when a row leaves the followed set: the page
  // keeps the figure after `useFrameLines` releases the line. At most one entry per row of the open list.
  const lastTicks = useRef(new Map<string, SymbolData>())
  if (follow) {
    const ticks = lastTicks.current
    const inList = new Set(itemKeys)
    for (const [key, line] of lines)
      if (inList.has(key) && priceOf(line)?.live) ticks.set(key, line)
    for (const key of ticks.keys()) if (!inList.has(key)) ticks.delete(key)
  }

  const rows: LiveRow[] = items.map((item) => {
    const key = keyOf(item.symbol, item.exchange)
    if (plan && !rowFollowed({ key, keys: strikes?.get(key) }, plan)) {
      // Not followed: the newer of the last tick it had and the last read, with the time of it, grey.
      const view = rowView(snapshot.quotes.get(key), lastTicks.current.get(key), basis)
      return { item, key, view, direction: null, stale: false, tickAt: null, outside: reason }
    }
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
            !row.outside && // F9: a row outside the followed set is never re-read on a timer
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
    const followedRows = rows.filter((row) => !row.outside) // F9
    line = statusLine({
      now,
      marketOpen: anyOpen(exchanges.map(openOf)),
      lastTickAt: latest(...rows.map((row) => row.tickAt)),
      lastUpdateAt: latest(...followedRows.map((row) => row.view.at)),
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
    readAll: snapshot.readAll, // F9
    lines, // F11
    directions, // F12
  }
}
