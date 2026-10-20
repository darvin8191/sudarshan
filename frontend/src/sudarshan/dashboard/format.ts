/**
 * Figures as a trader reads them: Indian grouping (1,23,456.79), a plus on a
 * gain where a sign is asked for, and a true minus (U+2212) on a loss, the same
 * width as the plus so a column of signed figures stays aligned. Zero is
 * neither: whatever rounds to 0.00 shows no sign and no direction.
 */
const MINUS = '−'

const formatters = new Map<number, Intl.NumberFormat>()

function grouping(decimals: number): Intl.NumberFormat {
  let formatter = formatters.get(decimals)
  if (!formatter) {
    formatter = new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    formatters.set(decimals, formatter)
  }
  return formatter
}

/** A figure from the API, which sends most of them as strings. Blank or not a number: null. */
export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

export type Direction = 'up' | 'down' | 'flat'

/** Which way a figure points once it is rounded the way it will be shown. */
export function directionOf(value: number, decimals = 2): Direction {
  const shown = Number(value.toFixed(decimals))
  if (shown > 0) return 'up'
  if (shown < 0) return 'down'
  return 'flat'
}

function sign(value: number, decimals: number, signed: boolean): string {
  const direction = directionOf(value, decimals)
  if (direction === 'down') return MINUS
  return signed && direction === 'up' ? '+' : ''
}

/** 1,23,456.79: the size of a figure, without a sign. */
export function formatAmount(value: number, decimals = 2): string {
  return grouping(decimals).format(Math.abs(value))
}

/** ₹48,320.50; −₹840.00 when negative, and +₹1,800.00 when `signed` and positive. */
export function formatRupees(value: number, { decimals = 2, signed = false } = {}): string {
  return `${sign(value, decimals, signed)}₹${formatAmount(value, decimals)}`
}

/** A price or level: 25,874.30, with a minus if it is ever negative. */
export function formatPrice(value: number, decimals = 2): string {
  return `${sign(value, decimals, false)}${formatAmount(value, decimals)}`
}

/** +0.62% or −0.18%, always signed unless it rounds to zero. */
export function formatPercent(value: number, decimals = 2): string {
  return `${sign(value, decimals, true)}${formatAmount(value, decimals)}%`
}
