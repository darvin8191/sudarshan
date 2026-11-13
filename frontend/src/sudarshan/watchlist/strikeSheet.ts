/**
 * What the strike sheet works out (M8.11, F12), free of React: IV and the Greeks of a pair, the
 * straddle, and the same strike on the next expiries.
 *
 * IV and Greeks come from `lib/optionGreeks.ts` (the option chain's own maths, with its own golden
 * vectors): the forward is the synthetic future this strike's CE and PE give by parity, the rate is the
 * option chain's (`CHAIN_RATE_PCT`), and a leg's price is its mid when it has a book, else its last.
 */
import type { MarketData } from '@/lib/MarketDataManager'
import {
  forwardFromParity,
  type Greeks,
  legGreeks,
  priceForGreeks,
  yearsToExpiry,
} from '@/lib/optionGreeks'
import type { SearchResult } from './api'
import type { Contract } from './v2/types'

/**
 * The risk-free rate the option chain page prices with: it passes none, so its default stands (0, the
 * server's `DEFAULT_INTEREST_RATES`). There is no preference to read yet; when the chain gains one,
 * this is the one place that follows it.
 */
export const CHAIN_RATE_PCT = 0

/** The words for a pair that cannot be priced: one side has no price. */
export const NO_IV = 'IV not worked out: no price on one side yet.'

/** How many later expiries the sheet shows. */
export const OTHER_EXPIRIES = 4

/** An expiry's session close, 15:30 IST (10:00 UTC), in epoch seconds. */
export function expiryTs(iso: string): number {
  const [year, month, day] = iso.split('-').map(Number)
  return Date.UTC(year, month - 1, day, 10, 0, 0) / 1000
}

export interface PairGreeks {
  /** The forward the parity gives. */
  forward: number
  ce: Greeks | null
  pe: Greeks | null
}

/** The price a leg's IV is worked out from, or 0 when it has none. */
export function legPrice(data: MarketData | undefined): number {
  return priceForGreeks(data?.ltp, data?.bid_price, data?.ask_price)
}

/** IV and Greeks of a pair, or null when either side has no price (the page says `NO_IV`). */
export function pairGreeks(args: {
  strike: number
  expiry: string
  ce: MarketData | undefined
  pe: MarketData | undefined
  ratePct?: number
}): PairGreeks | null {
  const cePrice = legPrice(args.ce)
  const pePrice = legPrice(args.pe)
  if (!(cePrice > 0) || !(pePrice > 0)) return null
  const forward = forwardFromParity(args.strike, cePrice, pePrice, args.strike)
  const t = yearsToExpiry(expiryTs(args.expiry))
  const rate = args.ratePct ?? CHAIN_RATE_PCT
  return {
    forward,
    ce: legGreeks('c', forward, args.strike, t, rate, cePrice),
    pe: legGreeks('p', forward, args.strike, t, rate, pePrice),
  }
}

/** "Straddle 340.55": the two last prices together; null while either is missing. */
export function straddleOf(ce: number | null, pe: number | null): number | null {
  return ce !== null && pe !== null && ce > 0 && pe > 0 ? ce + pe : null
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

/** "24-NOV-26" (the master's spelling) or "2026-11-24" as "2026-11-24"; null when it is neither. */
export function expiryIso(raw: string | null | undefined): string | null {
  if (!raw) return null
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw
  const match = /^(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})$/.exec(raw.trim())
  if (!match) return null
  const month = MONTHS.indexOf(match[2].toUpperCase()) + 1
  if (month === 0) return null
  const year = match[3].length === 2 ? 2000 + Number(match[3]) : Number(match[3])
  return `${year}-${String(month).padStart(2, '0')}-${match[1].padStart(2, '0')}`
}

/** One later expiry's pair at the same strike (a side the master lacks is null). */
export interface OtherExpiry {
  expiry: string
  ce: Contract | null
  pe: Contract | null
}

const positive = (value: unknown): number | null => {
  const n = typeof value === 'string' ? Number(value) : value
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null
}

/**
 * The same strike on the next `OTHER_EXPIRIES` expiries after `after`, from the instrument search's
 * rows for "<underlying> <strike>" (the page's own search; the server's `strikes` op is not on this
 * branch). Only the underlying's own options on the row's exchange count, and the strike must match.
 */
export function otherExpiries(
  found: readonly SearchResult[],
  args: { underlying: string; exchange: string; strike: number; after: string }
): OtherExpiry[] {
  const byExpiry = new Map<string, OtherExpiry>()
  for (const hit of found) {
    const expiry = expiryIso(hit.expiry)
    const right = hit.instrumenttype?.toUpperCase()
    if (!expiry || expiry <= args.after) continue
    if (right !== 'CE' && right !== 'PE') continue
    if (hit.exchange !== args.exchange || (hit.name ?? '').toUpperCase() !== args.underlying)
      continue
    if (Number(hit.strike) !== args.strike) continue
    const contract: Contract = {
      exchange: hit.exchange,
      symbol: hit.symbol,
      key: `${hit.exchange}:${hit.symbol}`,
      name: hit.name ?? args.underlying,
      lot: positive(hit.lotsize),
      tick: positive(hit.tick_size),
      expiry,
      isin: null,
    }
    const row = byExpiry.get(expiry) ?? { expiry, ce: null, pe: null }
    byExpiry.set(expiry, right === 'CE' ? { ...row, ce: contract } : { ...row, pe: contract })
  }
  return [...byExpiry.values()]
    .sort((a, b) => a.expiry.localeCompare(b.expiry))
    .slice(0, OTHER_EXPIRIES)
}
