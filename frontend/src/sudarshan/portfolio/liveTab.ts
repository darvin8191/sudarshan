/**
 * A tab moved by ticks (M7.24), kept free of React so every rule is a plain function a test calls.
 *
 * The service reads a tab at `readAt` and prices it at its own prices. The phone then moves the
 * figures with ticks newer than the read, as the dashboard's `liveMove` does for positions:
 *
 * - Only a WebSocket tick counts, and only one stamped after the read. A REST fallback answer, or
 *   a tick the read already included, leaves the service's price in place.
 * - A tick's price is the feed's float as `price_u` (paise x 100, `feedPriceU`); its previous
 *   close replaces the row's only when it carries one. A price that is no price (zero, negative,
 *   not a number) is no tick: the last good one stands.
 * - Only listed rows held now move, and only the first `LIVE_CAP` of them (200 on OpenAlgo's one
 *   pooled connection, cross-design R4). The rest keep the service's price and say when it was
 *   taken; they are never "Real-time".
 * - The sums are valuation.ts's, so a frame is the whole tab again, rounded the same way the
 *   service rounds it (vectors/valuation_ticks.json).
 */
import type { SymbolData } from '@/lib/MarketDataManager'
import { keyOf, priceOf } from '@/sudarshan/dashboard/live'
import { feedPriceU } from './money'
import type { AssetRef, TabTotals } from './types'
import { isHeld, tabTotals, type ValuationFixed, type ValuationRow } from './valuation'

/** Instruments a tab may subscribe: the portfolio's share of the pooled connection. */
export const LIVE_CAP = 200

/** A row as the live tab reads it: the sums' part, and what to subscribe. */
export interface LiveRowInput extends ValuationRow {
  asset: Pick<AssetRef, 'kind' | 'exchange' | 'symbol'>
}

/** The newest price of one instrument, as the cache holds it. */
export interface Tick {
  ltp: number
  prev_close: number | null
  at: number
  /** True for a WebSocket tick, false for the manager's REST fallback answer. */
  live: boolean
}

export type Ticks = ReadonlyMap<string, Tick>

/** The tick in a cache line, or null when it holds no usable price. */
export function tickOf(line: SymbolData | undefined): Tick | null {
  const price = priceOf(line)
  if (!price || feedPriceU(price.ltp) === null) return null
  const close = line?.data.prev_close
  return {
    ltp: price.ltp,
    prev_close: typeof close === 'number' && Number.isFinite(close) && close > 0 ? close : null,
    at: price.at,
    live: price.live,
  }
}

const instrumentKey = (row: LiveRowInput): string | null =>
  row.asset.exchange && row.asset.symbol ? keyOf(row.asset.symbol, row.asset.exchange) : null

/** The rows that can move with ticks: listed ones held now, the first `cap` of them. */
export function liveIndexes(rows: readonly LiveRowInput[], cap: number = LIVE_CAP): Set<number> {
  const picked: number[] = []
  rows.forEach((row, n) => {
    if (row.asset.kind === 'listed' && isHeld(row) && instrumentKey(row) !== null) picked.push(n)
  })
  return new Set(picked.slice(0, Math.max(0, cap)))
}

/** The instruments to subscribe, never more than `cap`. */
export function liveInstruments(
  rows: readonly LiveRowInput[],
  cap: number = LIVE_CAP
): Array<{ symbol: string; exchange: string }> {
  const out: Array<{ symbol: string; exchange: string }> = []
  for (const n of liveIndexes(rows, cap)) {
    out.push({ symbol: rows[n].asset.symbol as string, exchange: rows[n].asset.exchange as string })
  }
  return out
}

export interface MovedTab<T extends LiveRowInput> {
  /** The rows as priced now: the same objects where nothing moved. */
  rows: T[]
  /** Which rows a tick moved. */
  moved: boolean[]
  /** Held listed rows beyond the cap, which keep the service's price. */
  capped: number
  totals: TabTotals
}

/** The rows priced from the ticks newer than `readAt`, and the tab's figures at those prices. */
export function moveTab<T extends LiveRowInput>(
  rows: readonly T[],
  fixed: ValuationFixed,
  ticks: Ticks,
  readAt: number,
  cap: number = LIVE_CAP
): MovedTab<T> {
  const allowed = liveIndexes(rows, cap)
  const moved: boolean[] = []
  const priced = rows.map((row, n) => {
    const key = instrumentKey(row)
    const tick = key === null ? undefined : ticks.get(key)
    const price = tick ? feedPriceU(tick.ltp) : null
    if (!allowed.has(n) || !tick || !tick.live || tick.at <= readAt || price === null) {
      moved.push(false)
      return row
    }
    moved.push(true)
    const close = feedPriceU(tick.prev_close)
    return {
      ...row,
      price: {
        ...row.price,
        price_u: price,
        prev_close_u: close === null ? row.price.prev_close_u : close,
      },
    }
  })
  const held = rows.filter((row) => row.asset.kind === 'listed' && isHeld(row)).length
  return {
    rows: priced,
    moved,
    capped: Math.max(0, held - allowed.size),
    totals: tabTotals(priced, fixed),
  }
}
