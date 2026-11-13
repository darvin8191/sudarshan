/**
 * The analytics' words and short figures, free of React. A ratio the core could not work out says
 * why in words (never infinity, never a zero standing in for it); a figure with no meaning
 * yet says what to do (design 5.2, 6.2).
 */
import { formatRupees } from '@/sudarshan/dashboard/format'
import { type Metrics, PAISE_PER_RUPEE, type RatioState } from './types'

const STATE_WORDS: Record<Exclude<RatioState, 'ok'>, string> = {
  no_trades: 'No trades',
  no_losing_trades: 'No losing trades',
  no_variation: 'No variation',
  short: 'Too few days',
  no_capital: 'Set your trading capital',
}

/** A ratio's text, or the words for why it has none. */
export function ratioWords(value: string | null, state: RatioState, suffix = ''): string {
  if (state === 'ok' && value !== null) return `${value}${suffix}`
  return state === 'ok' ? 'No trades' : STATE_WORDS[state]
}

/** A big figure for a tile: whole rupees from ₹1,000 up so it fits, paise below that. */
export function tileRupees(paise: number, signed = true): string {
  const rupees = paise / PAISE_PER_RUPEE
  return formatRupees(rupees, { decimals: Math.abs(rupees) >= 1000 ? 0 : 2, signed })
}

const COMPACT = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 })

/** A signed figure short enough for a calendar cell: +812, −1.2k, +3.4L (lakh). Rupees, no symbol. */
export function compactPaise(paise: number): string {
  const rupees = Math.abs(paise) / PAISE_PER_RUPEE
  const rounded = Math.round(rupees)
  if (rounded === 0) return '0'
  const sign = paise < 0 ? '−' : '+'
  if (rounded >= 10_000_000) return `${sign}${COMPACT.format(rupees / 10_000_000)}Cr`
  if (rounded >= 100_000) return `${sign}${COMPACT.format(rupees / 100_000)}L`
  if (rounded >= 1000) return `${sign}${COMPACT.format(rupees / 1000)}k`
  return `${sign}${rounded}`
}

/** "R known for 4 of 9" and the average R, or what to do to get one. */
export function rWords(metrics: Pick<Metrics, 'r_known' | 'r_avg' | 'trips'>): {
  figure: string
  note: string
} {
  if (metrics.r_avg === null || metrics.r_known === 0) {
    return {
      figure: 'No R yet',
      note: metrics.trips === 0 ? 'No trades' : 'Add a risk to a trade to see R',
    }
  }
  const n = Number(metrics.r_avg)
  const figure = `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(2)} R`
  return { figure, note: `R known for ${metrics.r_known} of ${metrics.trips}` }
}

/** 20261113 as a chart's day "2026-11-13". */
export const chartDay = (day: number): string => {
  const t = String(day)
  return `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}`
}

/** The digits of a period key, for a day (8) or a month (6): tolerant of "2026-11-13" and "202611". */
export function digitsOf(key: string): string {
  return key.replace(/\D/g, '')
}

const MONTH = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** 202611 as "November 2026". */
export function monthWords(month: number): string {
  return MONTH.format(new Date(Date.UTC(Math.floor(month / 100), (month % 100) - 1, 1)))
}
