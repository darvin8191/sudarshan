import { useMemo, useRef } from 'react'
import { type PriceableItem, positionPnlAt } from '@/hooks/useLivePrice'
import type { FeedSummary } from '@/sudarshan/books/feed'
import {
  isClosedRow,
  type PriceSource,
  priceRow,
  type TickDirection,
  TickDirections,
  tickPrevClose,
} from '@/sudarshan/books/prices'
import { anyOpen, keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import { type Instrument, useFrameLines } from '@/sudarshan/live/useFrameLines'
import { useQuoteSnapshot } from '@/sudarshan/watchlist/useQuoteSnapshot'

/**
 * A book's rows priced tick by tick for the phone cards (M6.9), drawn at most
 * once a frame.
 *
 * Upstream's `useLivePrice` keeps every tick in the page's own state, so each
 * tick redraws the whole Positions, Holdings or Order book page. On a phone
 * the page hands its book to the cards and switches its own subscription off;
 * the cards subscribe here instead, through the watchlist's `useFrameLines`
 * (the tab's one shared market-data socket, LTP mode), so a tick redraws the
 * card list once per frame and nothing above it, and each card only when its
 * own figures moved.
 *
 * The figures are the books' own, unchanged: each row is priced by
 * `priceRow` (the live tick wins, the broker's P&L moved by the price change
 * since the book was read, a closed row as the broker reported it), and the
 * last tick's direction is the one `useLivePrice` shows, the first live tick
 * compared with the book's price. Within a frame the direction is the last
 * tick's, kept tick by tick by `useFrameLines`.
 *
 * `quotes` adds one REST quote snapshot per instrument (`useQuoteSnapshot`,
 * the watchlist's: read when an instrument first appears, after a reconnect,
 * on coming back to the page, at a market's open or close, never on a timer).
 * Holdings take it for the previous close, working orders for a price before
 * the first tick. Positions do not: the book carries the broker's own price.
 */

/** The exchanges whose opening and closing read the snapshot again, as for the watchlist. */
const MAIN_EXCHANGES = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX', 'CDS']

const NONE: Instrument[] = []

/** One row of a book, as it stands this frame. */
export interface PricedRow<T> {
  item: T
  /** EXCHANGE:SYMBOL, the market-data cache key. */
  key: string
  ltp: number | null
  pnl: number | null
  pnlPercent: number | null
  /** When the price shown was last confirmed, or null when nothing says. */
  at: number | null
  /** A WebSocket tick from this session of the feed brought the price shown. */
  live: boolean
  /** Upstream's name for where the price shown came from: the book's own is 'rest'. */
  source: PriceSource
  /** Which way the last tick moved the price, once it has moved. */
  tick: TickDirection | null
  /** The previous session's close: a tick's, else the quote snapshot's. */
  prevClose: number | null
  /** When its last WebSocket tick arrived, or null when none has. */
  tickAt: number | null
}

export interface FramePrices<T> {
  rows: PricedRow<T>[]
  /** What the status line reads (`useBookFeed`), over the rows that can still move. */
  feed: FeedSummary
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function positive(value: unknown): number | null {
  const n = finite(value)
  return n !== null && n > 0 ? n : null
}

export function useFramePrices<T extends PriceableItem>({
  items,
  bookAt,
  marketOpen,
  apiKey = null,
  quotes = false,
  enabled = true,
}: {
  items: readonly T[]
  /** When the book's own figures were asked for; only newer prices move them. */
  bookAt: number | null
  marketOpen: MarketOpen
  apiKey?: string | null
  /** Read a REST quote snapshot of the instruments too. */
  quotes?: boolean
  enabled?: boolean
}): FramePrices<T> {
  const instrumentsKey = [...new Set(items.map((item) => keyOf(item.symbol, item.exchange)))]
    .sort()
    .join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: instrumentsKey is the identity of the set; the book is a new array on every read
  const instruments = useMemo<Instrument[]>(() => {
    const seen = new Map<string, Instrument>()
    for (const { symbol, exchange } of items)
      seen.set(keyOf(symbol, exchange), { symbol, exchange })
    return [...seen.values()]
  }, [instrumentsKey])

  const on = enabled && instruments.length > 0
  const {
    lines,
    directions,
    feed: socket,
  } = useFrameLines({ instruments, mode: 'LTP', enabled: on })
  const snapshot = useQuoteSnapshot({
    apiKey: on && quotes ? apiKey : null,
    instruments: on && quotes ? instruments : NONE,
    connectionEpoch: socket.epoch,
    marketOpen: anyOpen(MAIN_EXCHANGES.map((exchange) => marketOpen(exchange))),
  })

  const seeds = useRef(new TickDirections())
  const epoch = socket.connected ? socket.epoch : null
  const rows = items.map((item): PricedRow<T> => {
    const key = keyOf(item.symbol, item.exchange)
    const line = lines.get(key)
    const quote = snapshot.quotes.get(key)
    const price = priceRow(
      item,
      {
        tick: line,
        quoteLtp: quote?.quote.ltp,
        quoteAt: quote?.at ?? null,
        quoteTradeAt: (quote?.quote as { last_trade_time?: unknown } | undefined)?.last_trade_time,
        bookAt,
        epoch,
      },
      positionPnlAt
    )
    let tick: TickDirection | null = null
    if (!isClosedRow(item)) {
      const tickLtp = positive(line?.data?.ltp)
      if (price.live && tickLtp !== null) {
        const seeded = seeds.current.observe(key, tickLtp, item.ltp ?? quote?.quote.ltp)
        tick = directions.get(key) ?? seeded
      } else {
        tick = directions.get(key) ?? seeds.current.get(key)
      }
    }
    return {
      item,
      key,
      ltp: positive(price.ltp),
      pnl: finite(price.pnl),
      pnlPercent: finite(price.pnlpercent),
      at: price.at,
      live: price.live,
      source: price.source,
      tick,
      prevClose: tickPrevClose(line?.data) ?? positive(quote?.quote.prev_close),
      tickAt:
        line?.updateSource === 'websocket' && typeof line.lastUpdate === 'number'
          ? line.lastUpdate
          : null,
    }
  })
  seeds.current.keep(new Set(rows.map((row) => row.key)))

  let lastTickAt: number | null = null
  let lastUpdateAt: number | null = null
  let due = 0
  for (const row of rows) {
    if (isClosedRow(row.item)) continue
    due += 1
    if (row.tickAt !== null && (lastTickAt === null || row.tickAt > lastTickAt)) {
      lastTickAt = row.tickAt
    }
    if (row.at !== null && (lastUpdateAt === null || row.at > lastUpdateAt)) lastUpdateAt = row.at
  }

  return { rows, feed: { lastTickAt, lastUpdateAt, due, connected: socket.connected } }
}
