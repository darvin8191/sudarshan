/**
 * The analytics' words and short figures, free of React. A ratio the core could not work out says
 * why in words (never infinity, never a zero standing in for it); a figure with no meaning
 * yet says what to do (design 5.2, 6.2).
 */
import { formatCompact, formatRupees } from '@/sudarshan/dashboard/format'
import { type Metrics, PAISE_PER_RUPEE, type RatioState } from './types'

const STATE_WORDS: Record<Exclude<RatioState, 'ok'>, string> = {
  no_trades: 'No trades',
  no_losing_trades: 'No losing trades',
  no_variation: 'No variation',
  short: 'Too few days',
  no_capital: 'Set your trading capital',
}

/** A ratio's text, or the words for why it has none; a state this page does not know reads "-". */
export function ratioWords(value: string | null, state: RatioState, suffix = ''): string {
  if (state === 'ok' && value !== null) return `${value}${suffix}`
  return state === 'ok' ? 'No trades' : (STATE_WORDS[state] ?? '-')
}

/** A big figure for a tile: whole rupees from ₹1,000 up so it fits, paise below that. */
export function tileRupees(paise: number, signed = true): string {
  const rupees = paise / PAISE_PER_RUPEE
  return formatRupees(rupees, { decimals: Math.abs(rupees) >= 1000 ? 0 : 2, signed })
}

/**
 * A signed figure short enough for a calendar or heat-map cell on a phone: +812, −1.2k, +12k,
 * +3.4L (lakh), +1.2Cr, or +340k and +12M when Display settings ask for international grouping
 * (M20.3). Two significant figures past a thousand, so a cell holds five characters and never
 * cuts one off (review M4); the cell's full figure is read out. Rupees, no symbol.
 */
export function compactPaise(paise: number): string {
  const rupees = Math.abs(paise) / PAISE_PER_RUPEE
  if (Math.round(rupees) === 0) return '0'
  return `${paise < 0 ? '−' : '+'}${formatCompact(rupees)}`
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
