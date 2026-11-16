import { numberGrouping, numberLocale } from './numberStyle'

/**
 * Figures as a trader reads them: Indian grouping (1,23,456.79) unless Display settings ask for
 * international grouping (123,456.79; M20.3, numberStyle.ts), a plus on a
 * gain where a sign is asked for, and a true minus (U+2212) on a loss, the same
 * width as the plus so a column of signed figures stays aligned. Zero is
 * neither: whatever rounds to 0.00 shows no sign and no direction.
 */
const MINUS = '−'

/** One `Intl.NumberFormat` per locale and shape, however many screens ask for it. */
const formatters = new Map<string, Intl.NumberFormat>()

function formatterFor(locale: string, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options)}`
  let formatter = formatters.get(key)
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, options)
    formatters.set(key, formatter)
  }
  return formatter
}

/** Something that formats a number: what a screen needs of an `Intl.NumberFormat`. */
export interface NumberText {
  format(value: number): string
}

/**
 * A number format in the grouping Display settings chose (M20.3), for a figure this file has no
 * helper for. It asks for the grouping each time it formats, so one kept in a module constant
 * still follows the switch; on the tick path that costs one comparison.
 */
export function groupedNumber(options: Intl.NumberFormatOptions = {}): NumberText {
  let locale = ''
  let formatter: Intl.NumberFormat | null = null
  return {
    format(value) {
      const wanted = numberLocale()
      if (formatter === null || wanted !== locale) {
        formatter = formatterFor(wanted, options)
        locale = wanted
      }
      return formatter.format(value)
    },
  }
}

/** One figure in the grouping Display settings chose, for a line drawn now and then. */
export function formatNumber(value: number, options: Intl.NumberFormatOptions = {}): string {
  return formatterFor(numberLocale(), options).format(value)
}

const byDecimals = new Map<number, NumberText>()

function grouping(decimals: number): NumberText {
  let formatter = byDecimals.get(decimals)
  if (!formatter) {
    formatter = groupedNumber({ minimumFractionDigits: decimals, maximumFractionDigits: decimals })
    byDecimals.set(decimals, formatter)
  }
  return formatter
}

const ONE_PLACE = groupedNumber({ maximumFractionDigits: 1 })

/** Under 10, one place ("1.2", "9.9"); from 10, whole ("12", "99"). */
const short = (value: number): string =>
  value < 9.95 ? ONE_PLACE.format(value) : String(Math.round(value))

const COMPACT_STEPS = {
  indian: [
    [1e3, 'k'],
    [1e5, 'L'],
    [1e7, 'Cr'],
  ],
  international: [
    [1e3, 'k'],
    [1e6, 'M'],
    [1e9, 'B'],
  ],
} as const

/**
 * The size of a figure, without a sign, in at most four characters below a crore: 812, 1.2k, 12k, then 3.4L
 * (lakh) and 1.2Cr in Indian grouping, or 340k and 12M in international grouping. Two
 * significant figures past a thousand; a step whose figure would round to a thousand moves up.
 */
export function formatCompact(value: number): string {
  const size = Math.abs(value)
  if (size < 999.5) return String(Math.round(size))
  const steps = COMPACT_STEPS[numberGrouping()]
  for (let i = 0; i < steps.length; i += 1) {
    const [unit, letter] = steps[i]
    const next = steps[i + 1]
    if (!next || size < next[0] * 0.995) return `${short(size / unit)}${letter}`
  }
  return String(Math.round(size))
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

const CONTRACT =
  /^(.+?)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}(?:(\d+(?:\.\d+)?)(CE|PE)|FUT)$/

/**
 * A derivative as a broker's app names it (SUDARSHAN D4): NIFTY17NOV2625900CE is "NIFTY 25,900 CE"
 * expiring "17 Nov", NIFTY24NOV26FUT "NIFTY FUT". Anything else keeps its symbol and no expiry.
 */
export function contractWords(symbol: string): { name: string; expiry: string | null } {
  const found = CONTRACT.exec(symbol.toUpperCase())
  if (!found) return { name: symbol, expiry: null }
  const [, base, day, month, strike, right] = found
  const expiry = `${Number(day)} ${month[0]}${month.slice(1).toLowerCase()}`
  if (strike === undefined) return { name: `${base} FUT`, expiry }
  const decimals = strike.includes('.') ? strike.split('.')[1].length : 0
  return { name: `${base} ${formatAmount(Number(strike), decimals)} ${right}`, expiry }
}
