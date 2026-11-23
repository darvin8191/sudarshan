/**
 * An expired contract on the phone chart (test day TD2, M4.15). OpenAlgo writes a future's or an
 * option's expiry in its symbol (NIFTY17NOV2625000CE), so from the next day in India it has no
 * prices, no bars and takes no orders, and trying again changes nothing. Pure.
 */
import { istDay } from '@/sudarshan/dashboard/live'

const DERIVATIVE_EXCHANGES = new Set(['NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NCDEX', 'NCO'])
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
/** Base, expiry DDMMMYY, then FUT or a strike and CE/PE. */
const CONTRACT = new RegExp(
  `^(.+?)(\\d{2})(${MONTHS.join('|')})(\\d{2})(?:FUT|(\\d+(?:\\.\\d+)?)(CE|PE))$`
)
/** Index underlyings, charted on their index exchange; any other base is a share. */
const INDICES = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50'])
const BSE_INDICES = new Set(['SENSEX', 'BANKEX', 'SENSEX50'])
const DAY_WORDS = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

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

export function isDerivative(exchange: string): boolean {
  return DERIVATIVE_EXCHANGES.has(exchange.toUpperCase())
}

/** A future or an option read from its symbol; null for anything else. */
export function contractOf(symbol: string, exchange: string): Contract | null {
  const found = isDerivative(exchange) ? CONTRACT.exec(symbol.toUpperCase()) : null
  if (!found) return null
  const [, base, day, month, year, strike, right] = found
  const expiry = `20${year}-${String(MONTHS.indexOf(month) + 1).padStart(2, '0')}-${day}`
  return strike ? { base, expiry, strike, right: right as 'CE' | 'PE' } : { base, expiry }
}

/** The day a contract expired, YYYY-MM-DD, once it is over in India; null while it trades. */
export function expiredOn(symbol: string, exchange: string, now = Date.now()): string | null {
  const contract = contractOf(symbol, exchange)
  return contract && contract.expiry < istDay(now) ? contract.expiry : null
}

/** "17 Nov 2026". */
export function dayWords(day: string): string {
  return DAY_WORDS.format(Date.parse(`${day}T12:00:00Z`))
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

/** The same contract on an expiry as the master's list writes it ("24-NOV-26"), or null. */
export function onExpiry(contract: Contract, expiry: string): string | null {
  const found = /^(\d{2})-([A-Z]{3})-(?:20)?(\d{2})$/.exec(expiry.trim().toUpperCase())
  if (!found || !MONTHS.includes(found[2])) return null
  const tail = contract.strike ? `${contract.strike}${contract.right}` : 'FUT'
  return `${contract.base}${found[1]}${found[2]}${found[3]}${tail}`
}
