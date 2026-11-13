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

/** One later expiry's pair at the same strike (a side the master lacks is null). */
export interface OtherExpiry {
  expiry: string
  ce: Contract | null
  pe: Contract | null
}
