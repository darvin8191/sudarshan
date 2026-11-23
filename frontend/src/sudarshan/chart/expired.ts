/**
 * An expired contract on the phone chart (test day TD2, M4.15). OpenAlgo writes a future's or an
 * option's expiry in its symbol (NIFTY17NOV2625000CE), so from the next day in India it has no
 * prices, no bars and takes no orders, and trying again changes nothing. The symbol is read by the
 * watchlist's own parser (`watchlist/rows.ts`, TD1), so a list and the chart call the same
 * contract expired on the same day. Pure.
 */
import { istDay } from '@/sudarshan/dashboard/live'
import {
  contractDay,
  contractParts,
  isDerivative,
  expiredOn as rowExpiredOn,
} from '@/sudarshan/watchlist/rows'

export { isDerivative }

/** Index underlyings, charted on their index exchange; any other base is a share. */
const INDICES = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50'])
const BSE_INDICES = new Set(['SENSEX', 'BANKEX', 'SENSEX50'])

export interface Contract {
  /** NIFTY, RELIANCE, CRUDEOILM. */
  base: string
  /** YYYY-MM-DD. */
  expiry: string
  /** An option's strike as its symbol writes it, and its side; neither for a future. */
  strike?: string
  right?: 'CE' | 'PE'
}

export interface Instrument {
  symbol: string
  exchange: string
}

/** A future or an option read from its symbol; null for anything else. */
export function contractOf(symbol: string, exchange: string): Contract | null {
  const parts = contractParts(symbol, exchange)
  const expiry = contractDay(symbol, exchange)
  return parts && expiry ? { ...parts, expiry } : null
}

/**
 * The day a contract expired, YYYY-MM-DD, once it is over in India, or on the day itself once the
 * master no longer lists it (`gone`); null while it trades. The watchlist's rule (`rows.expiredOn`).
 */
export function expiredOn(
  symbol: string,
  exchange: string,
  now = Date.now(),
  gone = false
): string | null {
  return rowExpiredOn(symbol, exchange, istDay(now), gone)
}

/** An index on its index exchange, a share on its cash one; null where it is a future (MCX). */
export function underlyingOf(contract: Contract, exchange: string): Instrument | null {
  const { base } = contract
  const venue = exchange.toUpperCase()
  if (venue === 'NFO') return { symbol: base, exchange: INDICES.has(base) ? 'NSE_INDEX' : 'NSE' }
  if (venue === 'BFO')
    return { symbol: base, exchange: BSE_INDICES.has(base) ? 'BSE_INDEX' : 'BSE' }
  return null
}

/**
 * The same contract on an expiry as the master's list writes it ("24-NOV-26"), or null; the symbol
 * built is read back by the same parser, so only a real contract symbol comes out.
 */
export function onExpiry(contract: Contract, expiry: string, exchange: string): string | null {
  const found = /^(\d{2})-([A-Z]{3})-(?:20)?(\d{2})$/.exec(expiry.trim().toUpperCase())
  if (!found) return null
  const tail = contract.strike ? `${contract.strike}${contract.right}` : 'FUT'
  const symbol = `${contract.base}${found[1]}${found[2]}${found[3]}${tail}`
  return contractDay(symbol, exchange) ? symbol : null
}
