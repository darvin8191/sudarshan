/**
 * Size by risk (B4: M5.11): how many whole lots (or shares, in cash) put no more than a chosen
 * sum at risk between the order's price and its stop. It only counts: the ticket's quantity is
 * filled when the owner asks, and nothing is ever sent from here.
 *
 * The price the risk is measured from is the one the order would fill near: a market order's
 * last price, a limit order's limit, and a stop order's trigger. The risk figure is kept per
 * segment on this phone, as the ticket's product is (productPref.ts): it is how this device
 * sizes, not something the account holds. Storage can be blocked; the figure then lasts for the
 * visit.
 */
import { formatRupees } from '@/sudarshan/dashboard/format'
import { type Segment, segmentOf } from './productPref'

export type Side = 'BUY' | 'SELL'

/** The size the risk allows, or why there is none. */
export type SizeAnswer =
  | {
      kind: 'size'
      /** Whole lots (shares in cash, where a lot is one). */
      lots: number
      /** The quantity in units: what the ticket's order carries. */
      units: number
      /** What the size puts at risk at this stop, in rupees. */
      atRisk: number
      /** The distance from the price to the stop, per unit. */
      perUnit: number
    }
  | { kind: 'why'; text: string }

/** A quotient a hair under a whole number is that number (0.1 + 0.2 and the like). */
const EPSILON = 1e-9

export function sizeByRisk({
  risk,
  stop,
  entry,
  side,
  lotSize,
}: {
  risk: number | null
  stop: number | null
  /** The price it would fill near, or null while it is not known. */
  entry: number | null
  side: Side
  lotSize: number
}): SizeAnswer | null {
  if (risk === null || !(risk > 0) || stop === null || !(stop > 0)) return null
  if (entry === null || !(entry > 0)) {
    return { kind: 'why', text: 'Waiting for the price to size from.' }
  }
  if (side === 'BUY' && stop >= entry) {
    return { kind: 'why', text: 'For a buy, the stop goes below the price.' }
  }
  if (side === 'SELL' && stop <= entry) {
    return { kind: 'why', text: 'For a sell, the stop goes above the price.' }
  }
  const lot = lotSize > 0 ? lotSize : 1
  const perUnit = Math.abs(entry - stop)
  const lots = Math.floor(risk / (perUnit * lot) + EPSILON)
  if (lots < 1) {
    const one = lot === 1 ? 'One share' : 'One lot'
    return {
      kind: 'why',
      text: `${one} risks ${formatRupees(perUnit * lot)} at this stop, more than ${formatRupees(risk)}.`,
    }
  }
  return { kind: 'size', lots, units: lots * lot, atRisk: lots * lot * perUnit, perUnit }
}

/**
 * The price the risk is measured from: a stop order's trigger, a limit order's limit, a market
 * order's last price; null while that is not known.
 */
export function entryOf({
  priceType,
  price,
  trigger,
  ltp,
}: {
  priceType: string
  price: number
  trigger: number
  ltp: number | undefined
}): number | null {
  const known = (value: number | undefined) => (value !== undefined && value > 0 ? value : null)
  if (priceType === 'SL' || priceType === 'SL-M') return known(trigger)
  if (priceType === 'LIMIT') return known(price)
  return known(ltp)
}

export const RISK_KEY = 'sudarshan-ticket-risk'

/** For a visit whose storage is blocked. */
let memory: Partial<Record<Segment, number>> = {}

function stored(): Partial<Record<Segment, number>> {
  try {
    const raw = localStorage.getItem(RISK_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Partial<Record<Segment, number>>) : {}
  } catch {
    return memory
  }
}

/** The last risk figure used for this exchange's segment on this phone, or null. */
export function readRisk(exchange: string): number | null {
  const segment = segmentOf(exchange)
  if (!segment) return null
  const risk = stored()[segment]
  return typeof risk === 'number' && Number.isFinite(risk) && risk > 0 ? risk : null
}

/** Keep a risk figure for this exchange's segment. Nothing is kept for one that is not a sum. */
export function rememberRisk(exchange: string, risk: number): void {
  const segment = segmentOf(exchange)
  if (!segment || !Number.isFinite(risk) || risk <= 0) return
  const next = { ...stored(), [segment]: risk }
  memory = next
  try {
    localStorage.setItem(RISK_KEY, JSON.stringify(next))
  } catch {
    // Storage refused: the figure lasts for the visit.
  }
}
