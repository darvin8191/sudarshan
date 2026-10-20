/**
 * The watchlist's live arithmetic (M8.2), kept free of React like the
 * dashboard's `live.ts`, whose rules it reuses: a watchlist row is an index
 * tile with a change in rupees beside the change in percent.
 *
 * Where a row's figures come from:
 * - **The level** is the newer of the REST snapshot and the newest tick
 *   (`indexView`), so a tick older than the read never undoes it.
 * - **The previous close** is a tick's `prev_close` whenever the tick carries
 *   one, and the snapshot's (Upstox `prev_close_price`) otherwise. Upstox ticks
 *   carry it in every mode once the completed tick payloads (M11.3) are in;
 *   until then, and for a frame without it, the snapshot gives it. A frame
 *   that leaves a field out never clears it: the market-data manager keeps the
 *   last value of every field, so nothing here falls back to zero.
 * - **The direction of the last tick** is worked out from the level shown,
 *   one render to the next (`tickDirection`).
 */
import type { QuotesData } from '@/api/trading'
import type { MarketData, SymbolData } from '@/lib/MarketDataManager'
import { directionOf, formatAmount } from '@/sudarshan/dashboard/format'
import { type IndexView, indexView } from '@/sudarshan/dashboard/live'

/** A tick's fields as this file reads them: upstream's, plus the previous close (M11.3). */
type Tick = MarketData & { prev_close?: number }

/** One instrument's REST quote, and when the read that brought it was sent. */
export interface Snapshot {
  quote: QuotesData
  at: number
}

/** One row as the list shows it. */
export interface RowView extends IndexView {
  /** The level less the previous close, in rupees. Null without both. */
  change: number | null
  /** The same as a percentage of the previous close. */
  changePercent: number | null
}

function positive(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

/**
 * A row from its snapshot and its cache line in the market-data manager. The
 * change is measured from a tick's previous close when the line holds one (a
 * missing, zero or non-numeric one does not count), and from the snapshot's
 * otherwise.
 */
export function rowView(snapshot: Snapshot | undefined, data: SymbolData | undefined): RowView {
  const view = indexView(snapshot?.quote, snapshot?.at ?? null, data)
  const prevClose = positive((data?.data as Tick | undefined)?.prev_close) ?? view.prevClose
  const change = view.ltp !== null && prevClose !== null ? view.ltp - prevClose : null
  return {
    ...view,
    prevClose,
    change,
    changePercent: change !== null && prevClose !== null ? (change / prevClose) * 100 : null,
  }
}

/** +12.40 or −3.05: a change in the instrument's own price, signed, no currency mark. */
export function formatChange(value: number): string {
  const direction = directionOf(value)
  return `${direction === 'up' ? '+' : direction === 'down' ? '−' : ''}${formatAmount(value)}`
}

export type TickDirection = 'up' | 'down'

/**
 * Which way the level moved last: up or down from the level before it. A tick
 * at the same level keeps the direction it had, as a broker's watchlist does,
 * and nothing is known until the level has moved once.
 */
export function tickDirection(
  before: number | null,
  after: number | null,
  previous: TickDirection | null
): TickDirection | null {
  if (before === null || after === null || before === after) return previous
  return after > before ? 'up' : 'down'
}

/** The segments whose symbols carry an expiry, and so a FUT or a strike and CE/PE. */
const DERIVATIVE_EXCHANGES = new Set(['NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NCDEX', 'NCO'])

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

/** OpenAlgo's derivative symbol: base, expiry DDMMMYY, then FUT or a strike and CE/PE. */
const CONTRACT = new RegExp(
  `^(.+?)(\\d{2})(${MONTHS.join('|')})(\\d{2})(?:(FUT)|(\\d+(?:\\.\\d+)?)(CE|PE))$`
)

export function isIndex(exchange: string): boolean {
  return exchange.toUpperCase().endsWith('_INDEX')
}

/** How a row names an instrument: a title, and the line under it. */
export interface Described {
  /** NIFTY 25000 CE, BANKNIFTY FUT, RELIANCE. */
  title: string
  /** NFO · 28 Oct 2026, NSE, NSE INDEX. */
  detail: string
  /** The underlying for a contract, the symbol itself otherwise. */
  underlying: string
}

/**
 * A contract read the way a trader says it: "NIFTY 25000 CE" over
 * "NFO · 28 Oct 2026" instead of NIFTY28OCT2625000CE, which does not fit a
 * phone row and is hard to read at a glance. Anything that is not a contract
 * keeps its symbol.
 */
export function describe(symbol: string, exchange: string): Described {
  const venue = exchange.toUpperCase().replace('_', ' ')
  const parsed = DERIVATIVE_EXCHANGES.has(exchange.toUpperCase()) ? CONTRACT.exec(symbol) : null
  if (!parsed) return { title: symbol, detail: venue, underlying: symbol }
  const [, base, day, month, year, future, strike, right] = parsed
  const expiry = `${Number(day)} ${month.charAt(0)}${month.slice(1).toLowerCase()} 20${year}`
  return {
    title: future ? `${base} FUT` : `${base} ${strike} ${right}`,
    detail: `${venue} · ${expiry}`,
    underlying: base,
  }
}

/** Where an instrument's options trade, for the option chain. */
const CHAIN_EXCHANGE: Record<string, string> = {
  NSE: 'NFO',
  NSE_INDEX: 'NFO',
  NFO: 'NFO',
  BSE: 'BFO',
  BSE_INDEX: 'BFO',
  BFO: 'BFO',
  MCX: 'MCX',
}

/**
 * The option chain an instrument belongs to: its underlying on the exchange
 * its options trade on. Whether that underlying has options at all is for the
 * exchange's list of underlyings to say; this only names the candidate.
 */
export function chainOf(
  symbol: string,
  exchange: string
): { exchange: string; underlying: string } | null {
  const venue = CHAIN_EXCHANGE[exchange.toUpperCase()]
  if (!venue) return null
  return { exchange: venue, underlying: describe(symbol, exchange).underlying }
}
