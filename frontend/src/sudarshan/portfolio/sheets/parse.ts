/**
 * What the owner types, as the integers the service takes (design 4.3): paise, qty_u (units x
 * 10^6) and price_u (rupees x 10^4), by string arithmetic so no float touches a figure. A text
 * that is not a number of the right shape is null, never a guess.
 */
import { PRICE_SCALE, QTY_SCALE } from '../types'

function parseScaled(text: string, decimals: number, scale: number): number | null {
  const clean = text.replace(/[,\s]/g, '')
  const match = /^(\d+)(?:\.(\d*))?$/.exec(clean)
  if (!match) return null
  const fraction = match[2] ?? ''
  if (fraction.length > decimals) return null
  const whole = Number(match[1])
  const frac = fraction === '' ? 0 : Number(fraction.padEnd(decimals, '0'))
  const out = whole * scale + frac
  return Number.isSafeInteger(out) ? out : null
}

/** "1,234.50" as 123450 paise; at most two decimals. */
export const parseRupees = (text: string): number | null => parseScaled(text, 2, 100)
/** "10.5" as qty_u; at most six decimals. */
export const parseQty = (text: string): number | null => parseScaled(text, 6, QTY_SCALE)
/** "1428.25" as price_u; at most four decimals. */
export const parsePrice = (text: string): number | null => parseScaled(text, 4, PRICE_SCALE)

/** Digits only, for a ratio. */
export function parseWhole(text: string): number | null {
  const clean = text.trim()
  if (!/^\d+$/.test(clean)) return null
  const n = Number(clean)
  return Number.isSafeInteger(n) && n > 0 ? n : null
}

/** A scaled integer back as the text it was typed as: trailing zeros of the fraction dropped. */
export function scaledText(value: number, scale: number, minDecimals = 0): string {
  const whole = Math.trunc(value / scale)
  const places = String(scale).length - 1
  let fraction = String(value % scale).padStart(places, '0')
  while (fraction.length > minDecimals && fraction.endsWith('0')) fraction = fraction.slice(0, -1)
  return fraction ? `${whole}.${fraction}` : String(whole)
}

export const rupeesInput = (paise: number): string => scaledText(paise, 100)
export const qtyInput = (qtyU: number): string => scaledText(qtyU, QTY_SCALE)
export const priceInput = (priceU: number): string => scaledText(priceU, PRICE_SCALE)

/** 20261104 as the date input's "2026-11-04", and back; null for anything else. */
export function dayToInput(day: number): string {
  const text = String(day)
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
}

export function inputToDay(text: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (!match) return null
  const month = Number(match[2])
  const day = Number(match[3])
  if (month < 1 || month > 12 || day < 1 || day > 31) return null
  return Number(`${match[1]}${match[2]}${match[3]}`)
}
