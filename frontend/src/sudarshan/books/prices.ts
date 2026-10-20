import type { PriceableItem } from '@/hooks/useLivePrice'
import type { MarketData, SymbolData } from '@/lib/MarketDataManager'

/**
 * How a book row's price and P&L are chosen (M6.2, M6.3), as plain functions a
 * test can call. `useLivePrice` applies them to every row of Positions,
 * Holdings and the /trading dock.
 *
 * **Newest wins.** A row can be priced from three places: the market-data
 * socket (a tick, or the manager's REST fallback during an outage), the REST
 * quote snapshot read when the page opened, and the row's own figures from the
 * broker's book. Whichever was confirmed last is shown. Upstream preferred a
 * tick only while it was under 5 seconds old and otherwise took a REST quote
 * up to 30 seconds old, so a quiet option could show an older price than the
 * tick it already had.
 *
 * **P&L is the broker's, moved by the market.** For an open row the P&L shown
 * is the broker's own figure for it plus what the price has done since the
 * book was read: `pnlAt(new price) - pnlAt(price in the book)`, both priced by
 * `positionPnlAt`. Upstream recomputed it from the average price and a
 * realised figure only the sandbox reports, which dropped the realised part of
 * a partly booked Upstox position as soon as a tick arrived. The difference
 * form is exact however the broker counts realised P&L or prices a position
 * carried from yesterday, because whatever it counted is still in its figure.
 * Only a price newer than the book moves it; a row closed to zero keeps the
 * broker's figures as they are.
 */

export type PriceSource = 'websocket' | 'multiquotes' | 'rest'

export type TickDirection = 'up' | 'down'

/** What `useLivePrice` adds to each row it prices. */
export interface LiveRowFields {
  /** Upstream's name for where the shown LTP came from. */
  _dataSource: PriceSource
  /** When the shown LTP was confirmed, or null when nothing says. */
  _at: number | null
  /** The shown LTP came from a WebSocket tick, the only source that is real-time. */
  _live: boolean
  /** Which way the last WebSocket tick moved the price, or null before it has moved. */
  _tick: TickDirection | null
}

export type PnlAt = (
  item: PriceableItem,
  ltp: number | undefined
) => { pnl: number; pnlPercent: number } | null

export interface RowSources {
  /** The market-data cache line for the row's instrument. */
  tick?: SymbolData
  /** The REST quote snapshot's last price. */
  quoteLtp?: number
  /** When the REST quote snapshot was asked for. */
  quoteAt?: number | null
  /** When the row's own figures (ltp, pnl) were read from the broker; null when not known. */
  bookAt?: number | null
}

export interface RowPrice {
  ltp: number | undefined
  pnl: number | undefined
  pnlpercent: number | undefined
  at: number | null
  live: boolean
  source: PriceSource
}

function positive(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) && n > 0 ? n : null
}

function finite(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

/** A row that carries a quantity is a position or holding; zero means closed. */
export function isClosedRow(item: PriceableItem): boolean {
  return item.quantity !== undefined && (Number(item.quantity) || 0) === 0
}

/**
 * The price and P&L to show for a row, from the newest of its sources, with
 * the P&L moved from the broker's own figure by the price change since the
 * book was read.
 */
export function priceRow(item: PriceableItem, sources: RowSources, pnlAt: PnlAt): RowPrice {
  const bookLtp = positive(item.ltp)
  const bookAt = sources.bookAt ?? null
  const asBooked: RowPrice = {
    ltp: item.ltp,
    pnl: item.pnl,
    pnlpercent: item.pnlpercent,
    at: bookAt,
    live: false,
    source: 'rest',
  }
  if (isClosedRow(item)) return asBooked

  // The newest price, and nothing that is not newer than the book.
  const floor = bookLtp === null ? Number.NEGATIVE_INFINITY : (bookAt ?? Number.NEGATIVE_INFINITY)
  let newest: { ltp: number; at: number; live: boolean; source: PriceSource } | null = null
  const tickLtp = positive(sources.tick?.data?.ltp)
  const tickAt = sources.tick?.lastUpdate
  if (tickLtp !== null && typeof tickAt === 'number' && tickAt > floor) {
    newest = {
      ltp: tickLtp,
      at: tickAt,
      live: sources.tick?.updateSource === 'websocket',
      source: 'websocket',
    }
  }
  const quoteLtp = positive(sources.quoteLtp)
  const quoteAt = sources.quoteAt
  if (
    quoteLtp !== null &&
    typeof quoteAt === 'number' &&
    quoteAt > floor &&
    (newest === null || quoteAt > newest.at)
  ) {
    newest = { ltp: quoteLtp, at: quoteAt, live: false, source: 'multiquotes' }
  }
  if (newest === null) return asBooked

  const priced: RowPrice = {
    ltp: newest.ltp,
    pnl: item.pnl,
    pnlpercent: item.pnlpercent,
    at: newest.at,
    live: newest.live,
    source: newest.source,
  }
  // A row with no quantity at all (a watchlist entry) has no P&L to move.
  if (item.quantity === undefined) return priced

  const bookPnl = finite(item.pnl)
  const now = pnlAt(item, newest.ltp)
  const then = bookLtp === null ? null : pnlAt(item, bookLtp)
  if (now && then && bookPnl !== null) {
    priced.pnl = bookPnl + (now.pnl - then.pnl)
  } else if (now && (bookLtp === null || bookPnl === null)) {
    // No book price or no book P&L to move from: the evaluator's own figure,
    // as upstream showed it.
    priced.pnl = now.pnl
  }
  const investment = Math.abs((Number(item.average_price) || 0) * (Number(item.quantity) || 0))
  if (investment > 0 && typeof priced.pnl === 'number') {
    priced.pnlpercent = (priced.pnl / investment) * 100
  }
  return priced
}

/**
 * Which way each instrument's WebSocket price last moved, kept between
 * renders. Asking again with the same price changes nothing, so it is safe to
 * call while rendering.
 */
export class TickDirections {
  private readonly last = new Map<string, { price: number; direction: TickDirection | null }>()

  /**
   * The direction of the last change in `price` for `key`. The first price
   * seen is compared with `seed` (the price shown before it, such as the
   * book's), when there is one.
   */
  observe(key: string, price: number, seed?: number | null): TickDirection | null {
    const seen = this.last.get(key)
    if (!seen) {
      const base = positive(seed)
      const direction = base === null || base === price ? null : price > base ? 'up' : 'down'
      this.last.set(key, { price, direction })
      return direction
    }
    if (price === seen.price) return seen.direction
    const direction: TickDirection = price > seen.price ? 'up' : 'down'
    this.last.set(key, { price, direction })
    return direction
  }

  /** The last direction known for `key`, without observing anything. */
  get(key: string): TickDirection | null {
    return this.last.get(key)?.direction ?? null
  }

  /** Forget instruments no longer shown. */
  keep(keys: ReadonlySet<string>): void {
    for (const key of this.last.keys()) if (!keys.has(key)) this.last.delete(key)
  }
}

/**
 * Optional fields a tick may carry, read defensively: the Upstox adapter is
 * gaining previous close, open interest and volume on Quote and Depth ticks,
 * and an older build sends none of them. Zero and absent both mean "not sent".
 */
export function tickNumber(data: MarketData | undefined, field: string): number | null {
  if (!data) return null
  return positive((data as Record<string, unknown>)[field])
}

/**
 * A count a tick carries (open interest, volume), zero included; null when
 * the tick does not carry it at all.
 */
export function tickCount(data: MarketData | undefined, field: string): number | null {
  if (!data) return null
  const value = (data as Record<string, unknown>)[field]
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

/**
 * The previous session's close from a tick: `prev_close`, or `cp` as LTP-mode
 * ticks name it. Never `close`: today's Upstox adapter fills it with the
 * running day bar, and the completed feed with the same previous close, or 0
 * when it is not known.
 */
export function tickPrevClose(data: MarketData | undefined): number | null {
  return tickNumber(data, 'prev_close') ?? tickNumber(data, 'cp')
}
