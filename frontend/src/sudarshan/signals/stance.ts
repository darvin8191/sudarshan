// SUDARSHAN M13.9 (P8 U6): the owner's open index F&O against TRINETRA's regime, worked out in the
// browser from the position book the app already reads (design 3.7.5). Direction only, no size
// weighting (design section 14): futures by their sign; long calls and short puts count as long;
// long puts and short calls as short; long and short together are mixed. Then beside the regime's
// bias: "with the regime" or "against the regime". The vectors in stance.vectors.json pin the rule.
import type { Bias, Instrument } from './types'

export type Side = 'long' | 'short'
export type LegKind = 'future' | 'call' | 'put'

export interface Leg {
  instrument: Instrument
  kind: LegKind
  side: Side
}

/** Where the owner stands on one index: long, short, both (mixed) or nothing open. */
export type Direction = 'long' | 'short' | 'mixed' | 'none'

/**
 * His positions beside the regime: with it, against it, mixed (long and short together), the
 * regime neutral, nothing open, or no regime reading to compare with.
 */
export type Stance = 'with' | 'against' | 'mixed' | 'neutral' | 'none' | 'unknown'

/** An index F&O contract in OpenAlgo's form: NIFTY17NOV2625900CE, BANKNIFTY25NOV26FUT. */
const CONTRACT =
  /^(NIFTY|BANKNIFTY)\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}(?:\d+(?:\.\d+)?(CE|PE)|FUT)$/

/** The index a contract or index row belongs to, for the indices TRINETRA reads; null for any other. */
export function underlyingOf(symbol: unknown, exchange: unknown): Instrument | null {
  if (typeof symbol !== 'string' || typeof exchange !== 'string') return null
  const name = symbol.trim().toUpperCase()
  const where = exchange.trim().toUpperCase()
  if (where === 'NSE_INDEX') return name === 'NIFTY' || name === 'BANKNIFTY' ? name : null
  if (where !== 'NFO') return null
  const found = CONTRACT.exec(name)
  return found ? (found[1] as Instrument) : null
}

/** One open row of the position book as a leg on an index; null for anything else or a closed row. */
export function legOf(row: {
  symbol?: unknown
  exchange?: unknown
  quantity?: unknown
}): Leg | null {
  if (typeof row.symbol !== 'string' || typeof row.exchange !== 'string') return null
  if (row.exchange.trim().toUpperCase() !== 'NFO') return null
  const found = CONTRACT.exec(row.symbol.trim().toUpperCase())
  if (!found) return null
  const quantity =
    typeof row.quantity === 'number'
      ? row.quantity
      : typeof row.quantity === 'string' && row.quantity.trim() !== ''
        ? Number(row.quantity)
        : Number.NaN
  if (!Number.isFinite(quantity) || quantity === 0) return null
  const bought = quantity > 0
  const right = found[2]
  const kind: LegKind = right === 'CE' ? 'call' : right === 'PE' ? 'put' : 'future'
  // A put moves against its index: bought it is short the index, sold it is long.
  const side: Side = kind === 'put' ? (bought ? 'short' : 'long') : bought ? 'long' : 'short'
  return { instrument: found[1] as Instrument, kind, side }
}

/** The legs on one index and the direction they add up to. */
export function directionOf(
  rows: ReadonlyArray<{ symbol?: unknown; exchange?: unknown; quantity?: unknown }>,
  instrument: Instrument
): { direction: Direction; legs: Leg[] } {
  const legs = rows.flatMap((row) => {
    const leg = legOf(row)
    return leg && leg.instrument === instrument ? [leg] : []
  })
  const long = legs.some((leg) => leg.side === 'long')
  const short = legs.some((leg) => leg.side === 'short')
  const direction: Direction = long && short ? 'mixed' : long ? 'long' : short ? 'short' : 'none'
  return { direction, legs }
}

/** The direction beside the regime's bias (null: no regime reading to compare with). */
export function stanceOf(direction: Direction, bias: Bias | null): Stance {
  if (direction === 'none') return 'none'
  if (bias === null) return 'unknown'
  if (direction === 'mixed') return 'mixed'
  if (bias === 'neutral') return 'neutral'
  return (direction === 'long') === (bias === 'bull') ? 'with' : 'against'
}

/** The words of each stance, as the card says them beside its icon. */
export const STANCE_WORDS: Record<Stance, string> = {
  with: 'With the regime',
  against: 'Against the regime',
  mixed: 'Mixed: long and short together',
  neutral: 'The regime reads neutral',
  none: 'No open position',
  unknown: 'No regime reading to compare with',
}

/** "You are long NIFTY." and the like: where he stands, without sizes. */
export function directionWords(direction: Direction, instrument: Instrument): string {
  switch (direction) {
    case 'long':
      return `You are long ${instrument}.`
    case 'short':
      return `You are short ${instrument}.`
    case 'mixed':
      return `You hold long and short ${instrument} positions.`
    default:
      return `No open ${instrument} futures or options.`
  }
}

/** What the legs are, in a broker's words: "futures bought, calls sold". */
export function legWords(legs: readonly Leg[]): string {
  const parts: string[] = []
  const kinds: ReadonlyArray<[LegKind, string]> = [
    ['future', 'futures'],
    ['call', 'calls'],
    ['put', 'puts'],
  ]
  for (const [kind, name] of kinds) {
    const bought = legs.some(
      (leg) => leg.kind === kind && (kind === 'put' ? leg.side === 'short' : leg.side === 'long')
    )
    const sold = legs.some(
      (leg) => leg.kind === kind && (kind === 'put' ? leg.side === 'long' : leg.side === 'short')
    )
    if (bought) parts.push(`${name} bought`)
    if (sold) parts.push(`${name} sold`)
  }
  return parts.join(', ')
}
