/**
 * Money, quantities and prices as integers, the phone's copy of core/money.py (M7.24).
 *
 * A figure is paise, `qty_u` (units x 10^6) or `price_u` (rupees x 10^4). Products can pass 2^53,
 * so every multiplication is a BigInt, and the one division, `roundDiv`, rounds half away from zero:
 * half a paisa is always a paisa more, and a loss mirrors a gain. `Math.round` and floats never
 * touch a figure the owner reads. tests/vectors/money.json runs against both languages.
 */
import { PCT_DECIMALS, VALUE_DIVISOR } from './types'

const PCT_SCALE = 10n ** BigInt(PCT_DECIMALS)

/** `numerator / denominator` as a whole number, half away from zero. */
export function roundDiv(numerator: bigint, denominator: bigint): bigint {
  let n = numerator
  let d = denominator
  if (d < 0n) {
    n = -n
    d = -d
  }
  const magnitude = n < 0n ? -n : n
  let whole = magnitude / d
  if (2n * (magnitude % d) >= d) whole += 1n
  return n >= 0n ? whole : -whole
}

/** What `qtyU` units are worth at `priceU`, in paise. */
export function valuePaise(qtyU: number, priceU: number): number {
  return Number(roundDiv(BigInt(qtyU) * BigInt(priceU), BigInt(VALUE_DIVISOR)))
}

/** A percentage counted in ten-thousandths of a percent as text: 80000 -> "8.0000". */
export function formatPct(scaled: bigint): string {
  const magnitude = scaled < 0n ? -scaled : scaled
  const whole = magnitude / PCT_SCALE
  const fraction = (magnitude % PCT_SCALE).toString().padStart(PCT_DECIMALS, '0')
  return `${scaled < 0n ? '-' : ''}${whole}.${fraction}`
}

/** `numerator / denominator` as a percentage text with 4 decimals; null with nothing to divide by. */
export function pct(numerator: number, denominator: number): string | null {
  if (denominator === 0) return null
  return formatPct(roundDiv(BigInt(numerator) * 100n * PCT_SCALE, BigInt(denominator)))
}

/** Python's `round` on a float: halves go to the even neighbour, not up. */
export function roundHalfEven(x: number): number {
  const floor = Math.floor(x)
  const rest = x - floor
  if (rest < 0.5) return floor
  if (rest > 0.5) return floor + 1
  return floor % 2 === 0 ? floor : floor + 1
}

/**
 * A price from the feed as `price_u`: paise(v) x 100, the way the alerts count it
 * (core/money.py `feed_price_u`). Null unless it is a finite number of at least one paisa.
 */
export function feedPriceU(value: unknown): number | null {
  if (typeof value !== 'number' || !(value > 0) || !Number.isFinite(value)) return null
  const paise = roundHalfEven(value * 100)
  return paise ? paise * 100 : null
}
