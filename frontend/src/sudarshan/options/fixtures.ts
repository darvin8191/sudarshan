/** Synthetic chains for the options tests: every symbol and figure is made up. Not a test file. */
import type { Listing, ServerChain } from './api'
import type { SnapLeg } from './model'

export const DAY_MS = 86_400_000
/** Monday 16 Nov 2026, 10:42:05 in India. */
export const NOW = Date.UTC(2026, 10, 16, 5, 12, 5)
export const EXPIRY = '2026-11-17'
/** 15:40 in India on the expiry day. */
export const STOPS_AT = Date.UTC(2026, 10, 17, 10, 10, 0)

export const LISTING: Listing = {
  underlying: 'NIFTY',
  exchange: 'NFO',
  spot: { symbol: 'NIFTY', exchange: 'NSE_INDEX' },
  future: { symbol: 'NIFTY24NOV26FUT', exchange: 'NFO', expiry: '2026-11-24' },
  expiries: [
    { date: EXPIRY, stops_at: STOPS_AT },
    { date: '2026-11-24', stops_at: STOPS_AT + 7 * DAY_MS },
    { date: '2026-12-29', stops_at: STOPS_AT + 42 * DAY_MS },
  ],
  now: NOW,
}

export const symbolOf = (strike: number, side: 'CE' | 'PE') => `NIFTY17NOV26${strike}${side}`

function leg(strike: number, side: 'CE' | 'PE', spot: number): SnapLeg {
  const intrinsic = Math.max(side === 'CE' ? spot - strike : strike - spot, 0)
  const ltp = Math.round((intrinsic + 40 + 2000 / (10 + Math.abs(strike - spot) / 10)) * 20) / 20
  const oi = 65 * (200 + ((strike / 50) % 7) * 150 + (side === 'CE' ? 30 : 90))
  return {
    symbol: symbolOf(strike, side),
    exchange: 'NFO',
    lot: 65,
    tick: 0.05,
    ltp,
    bid: ltp - 0.05,
    ask: ltp + 0.05,
    oi,
    prev_oi: oi - 65 * (side === 'CE' ? 20 : -15),
    volume: 1000,
    close: ltp - (side === 'CE' ? 4 : -3),
  }
}

/** A chain of `count` strikes, 50 apart, from `from`, with the spot at `spot`. */
export function chainOf({
  count = 61,
  from = 24_500,
  spot = 25_912.35,
  asOf = NOW,
}: {
  count?: number
  from?: number
  spot?: number
  asOf?: number
} = {}): ServerChain {
  return {
    as_of: asOf,
    exchange: 'NFO',
    underlying: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    expiry: EXPIRY,
    expiry_ts: STOPS_AT,
    spot,
    pcr: null,
    left_out: 0,
    by_strike: 0,
    strikes: Array.from({ length: count }, (_, i) => {
      const strike = from + i * 50
      return { strike, ce: leg(strike, 'CE', spot), pe: leg(strike, 'PE', spot) }
    }),
  }
}
