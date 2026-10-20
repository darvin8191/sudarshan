/**
 * The dashboard's live arithmetic, kept free of React so every rule is a plain
 * function a test can call.
 *
 * Where the numbers come from:
 * - **Ticks** reach the page through `useMarketData` and the one shared
 *   `MarketDataManager` socket per tab. A cache line records whether the
 *   WebSocket wrote it or the manager's REST fallback did; only the WebSocket
 *   counts as live.
 * - **The broker's own figures** (funds, realised and unrealised P&L, the
 *   position book) are read over REST when the page opens, after every order
 *   event and every 45 seconds while a market is open. They are the source of
 *   truth: each re-read replaces whatever the ticks had moved.
 * - **Between re-reads**, unrealised P&L moves with every tick by the change in
 *   each open position's P&L from the price the position book reported to the
 *   price just ticked, priced by `positionPnlAt`, the evaluator the Positions
 *   page uses (long, short, the contract multiplier). Today's realised P&L is
 *   not touched by ticks: only a trade changes it, and a trade raises an order
 *   event that re-reads the broker.
 */
import { positionPnlAt } from '@/hooks/useLivePrice'
import type { SymbolData } from '@/lib/MarketDataManager'
import type { Position } from '@/types/trading'

/** A symbol with no tick for this long, while its market is open, is shown as stale. */
export const STALE_MS = 10_000

/** How often funds and positions are re-read from the broker while a market is open. */
export const RESYNC_MS = 45_000

/** The cache key `MarketDataManager` and `useMarketData` use. */
export const keyOf = (symbol: string, exchange: string) =>
  `${exchange.toUpperCase()}:${symbol.toUpperCase()}`

/** The newest price a cache line holds, when it arrived, and whether a tick brought it. */
export interface Price {
  ltp: number
  at: number
  /** True for a WebSocket tick, false for the manager's REST fallback poll. */
  live: boolean
}

/** The price in a cache line, or null when it holds none worth showing. */
export function priceOf(data: SymbolData | undefined): Price | null {
  if (!data || data.lastUpdate === undefined) return null
  const ltp = data.data.ltp
  if (typeof ltp !== 'number' || !Number.isFinite(ltp) || ltp <= 0) return null
  return { ltp, at: data.lastUpdate, live: data.updateSource === 'websocket' }
}

/** The position book as read at `at`, when the request went out. */
export interface PositionsSnapshot {
  positions: Position[]
  at: number
}

/** Positions still held: a quantity that is present and not zero. */
export function openPositions(positions: Position[]): Position[] {
  return positions.filter((position) => Number(position.quantity) !== 0)
}

export interface LiveMove {
  /** Unrealised P&L made or lost since the snapshot, at prices that arrived after it. */
  delta: number
  /** How many positions are open in the snapshot. */
  open: number
  /** The newest WebSocket tick for any open position, or null when none has come. */
  lastTickAt: number | null
}

/**
 * How far the open positions have moved since the broker last reported them.
 *
 * For each open position, a price newer than the snapshot is compared with the
 * price the position book carried: the difference in `positionPnlAt` between
 * the two is what the position has made or lost since. Summed over positions
 * this is exact for longs and shorts alike, and it leaves the broker's own
 * unrealised figure as the starting point, however the broker computes it
 * (from the average price, or from yesterday's close for a carried position).
 *
 * A position the evaluator cannot price (no average price, as upstream) or one
 * whose snapshot price is missing does not move until the next re-read.
 */
export function liveMove(
  snapshot: PositionsSnapshot | null,
  prices: Map<string, SymbolData>
): LiveMove {
  if (!snapshot) return { delta: 0, open: 0, lastTickAt: null }
  let delta = 0
  let lastTickAt: number | null = null
  const held = openPositions(snapshot.positions)
  for (const position of held) {
    const price = priceOf(prices.get(keyOf(position.symbol, position.exchange)))
    if (!price) continue
    if (price.live && (lastTickAt === null || price.at > lastTickAt)) lastTickAt = price.at
    if (price.at <= snapshot.at) continue
    const reported = Number(position.ltp)
    if (!(reported > 0)) continue
    const now = positionPnlAt(position, price.ltp)
    const then = positionPnlAt(position, reported)
    if (now && then) delta += now.pnl - then.pnl
  }
  return { delta, open: held.length, lastTickAt }
}

/** One index as the strip shows it. */
export interface IndexView {
  /** The level shown: the newest of the snapshot and the ticks. */
  ltp: number | null
  /**
   * The previous session's close: a tick's `prev_close` whenever the cache line
   * holds one (Upstox ticks carry it in every mode, M11.3), the snapshot's
   * otherwise.
   */
  prevClose: number | null
  /** When the level shown was last confirmed. */
  at: number | null
  /** Whether a WebSocket tick brought the level shown. */
  live: boolean
}

/** A previous close worth measuring a change from: a finite number above zero. */
function closeOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

/**
 * Merge an index's REST snapshot with its ticks: whichever is newer gives the
 * level. The previous close is a tick's `prev_close` whenever the cache line
 * holds one, however old, since it is the same figure all session and Upstox
 * ticks carry it in every mode (M11.3); the snapshot's otherwise. A missing,
 * zero or non-numeric one never counts, so a change is never measured from zero.
 */
export function indexView(
  snapshot: { ltp: number; prev_close: number } | undefined,
  snapshotAt: number | null,
  data: SymbolData | undefined
): IndexView {
  const prevClose = closeOf(data?.data.prev_close) ?? closeOf(snapshot?.prev_close)
  const price = priceOf(data)
  if (price && (snapshotAt === null || price.at >= snapshotAt)) {
    return { ltp: price.ltp, prevClose, at: price.at, live: price.live }
  }
  if (snapshot && snapshot.ltp > 0) {
    return { ltp: snapshot.ltp, prevClose, at: snapshotAt, live: false }
  }
  return { ltp: null, prevClose, at: null, live: false }
}

/**
 * Stale: the market is known to be open and the level shown is more than
 * `STALE_MS` old, unless a tick brought it and the feed is still delivering
 * the strip's other ticks (`feedTickAt`, the newest of them): an index that has
 * not moved can go quiet for a while on a feed that is fine. A level only the
 * REST read gave, or any level once the whole feed has gone quiet, is stale. A
 * closed or unknown market makes nothing stale, because then no tick is due.
 */
export function isStale(
  view: IndexView,
  now: number,
  marketOpen: boolean | null,
  feedTickAt: number | null
): boolean {
  if (marketOpen !== true || view.ltp === null) return false
  if (view.at !== null && now - view.at <= STALE_MS) return false
  const feedAlive = feedTickAt !== null && now - feedTickAt <= STALE_MS
  return !(view.live && feedAlive)
}

/** Open, closed, or not known (the timings are loading, failed, or were read on an earlier day). */
export type MarketOpen = (exchange: string) => boolean | null

/** Open if any is open, closed if all are known closed, otherwise not known. */
export function anyOpen(states: Array<boolean | null>): boolean | null {
  if (states.some((state) => state === true)) return true
  if (states.length > 0 && states.every((state) => state === false)) return false
  return null
}

/** The calendar date in India, as YYYY-MM-DD. */
export function istDay(at: number): string {
  return new Date(at + 5.5 * 3_600_000).toISOString().slice(0, 10)
}

/** What the live line beside a figure says. Each kind is a word as well as a mark. */
export type FeedLine =
  | { kind: 'live' }
  | { kind: 'delayed'; at: number; stale: boolean }
  /**
   * The socket is down and a quote request every few seconds is filling in:
   * "Delayed", with the age of the last answer. Only a screen that runs such a
   * fallback (the chart) says so; the others never pass `polling`.
   */
  | { kind: 'polling'; at: number; stale: boolean }
  /** The socket is not up: on first load, after the phone wakes, or while the feed is down. */
  | { kind: 'connecting'; at: number | null; stale: boolean; again: boolean }
  | { kind: 'waiting' }
  | { kind: 'closed' }

/**
 * The live line for a set of instruments: "Live" only while WebSocket ticks
 * are arriving, never on the strength of a REST answer. While the socket is
 * down and ticks are due it says it is (re)connecting, or delayed when a quote
 * request is filling in, and how old the figures are; otherwise it gives their
 * age. Any of those is called stale when the market is known to be open, ticks
 * are due, and nothing has come for `STALE_MS` (or the screen's `staleMs`).
 */
export function feedLine({
  now,
  marketOpen,
  lastTickAt,
  lastUpdateAt,
  ticksDue = true,
  connected = true,
  polling = false,
  staleMs = STALE_MS,
}: {
  now: number
  marketOpen: boolean | null
  lastTickAt: number | null
  lastUpdateAt: number | null
  /** False when nothing is held, so nothing should tick and nothing can go stale. */
  ticksDue?: boolean
  /** Whether the market-data socket is up and signed in. */
  connected?: boolean
  /**
   * Whether a quote request every few seconds is standing in for the socket
   * while it is down. With an answer in hand the line says "Delayed" and its
   * age rather than "Reconnecting".
   */
  polling?: boolean
  /**
   * How long without a tick, in market hours, before the figures are stale.
   * The dashboard's own `STALE_MS` unless a screen knows its instrument better
   * (a chart of a thinly traded stock goes quiet for longer than an index).
   */
  staleMs?: number
}): FeedLine {
  if (marketOpen === false) return { kind: 'closed' }
  const old = (at: number | null) =>
    ticksDue && marketOpen === true && (at === null || now - at > staleMs)
  if (ticksDue && !connected) {
    if (polling && lastUpdateAt !== null)
      return { kind: 'polling', at: lastUpdateAt, stale: old(lastUpdateAt) }
    return {
      kind: 'connecting',
      at: lastUpdateAt,
      stale: old(lastUpdateAt),
      again: lastTickAt !== null,
    }
  }
  if (lastTickAt !== null && now - lastTickAt <= staleMs) return { kind: 'live' }
  if (lastUpdateAt !== null) return { kind: 'delayed', at: lastUpdateAt, stale: old(lastUpdateAt) }
  return { kind: 'waiting' }
}

/**
 * Whether two lines say the same thing apart from the age they show, which
 * moves every second: what a screen re-renders on, or a marker announces.
 */
export function sameFeedState(a: FeedLine, b: FeedLine): boolean {
  if (a.kind !== b.kind) return false
  const stale = (line: FeedLine) => ('stale' in line ? line.stale : false)
  const again = (line: FeedLine) => (line.kind === 'connecting' ? line.again : false)
  return stale(a) === stale(b) && again(a) === again(b)
}

/** The latest of some moments, or null when none of them is known. */
export function latest(...moments: Array<number | null | undefined>): number | null {
  let newest: number | null = null
  for (const moment of moments) {
    if (typeof moment === 'number' && (newest === null || moment > newest)) newest = moment
  }
  return newest
}

/** "4s ago", "3m ago", "2h ago". */
export function ago(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.round(minutes / 60)}h ago`
}
