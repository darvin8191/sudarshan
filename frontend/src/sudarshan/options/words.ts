/**
 * The words and figures of the options screen (M11.1), free of React. Numbers are en-IN; open
 * interest is said in lakh and crore, as a trader reads it.
 */
import { formatAmount } from '@/sudarshan/dashboard/format'
import { istDay } from '@/sudarshan/dashboard/live'

const SHORT_DAY = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
})

const DAY_MS = 86_400_000
const noon = (iso: string) => Date.parse(`${iso}T12:00:00Z`)

/** Whole calendar days in India from today to the expiry day; 0 on the day itself. */
export function daysLeft(expiry: string, now: number): number {
  return Math.round((noon(expiry) - noon(istDay(now))) / DAY_MS)
}

/** "17 Nov": the expiry day. */
export function expiryDay(expiry: string): string {
  return SHORT_DAY.format(noon(expiry))
}

/** "17 Nov · 3d", and "17 Nov · today" on the expiry day. */
export function expiryLabel(expiry: string, now: number): string {
  const left = daysLeft(expiry, now)
  return `${expiryDay(expiry)} · ${left <= 0 ? 'today' : `${left}d`}`
}

/** What a screen reader hears of an expiry chip. */
export function expirySpeech(expiry: string, now: number): string {
  const left = daysLeft(expiry, now)
  const when = left <= 0 ? 'expires today' : left === 1 ? '1 day left' : `${left} days left`
  return `${expiryDay(expiry)}, ${when}`
}

/** Open interest in lakh and crore: "42.1 L", "1.12 Cr"; below a lakh, the number itself. */
export function oiWords(value: number): string {
  const size = Math.abs(value)
  if (size >= 1e7) return `${formatAmount(size / 1e7, 2)} Cr`
  if (size >= 1e5) return `${formatAmount(size / 1e5, 1)} L`
  return formatAmount(size, 0)
}

/** A strike as it is listed: no decimals unless it has them ("25,900", "102.5"). */
export function strikeWords(strike: number): string {
  return formatAmount(
    strike,
    Number.isInteger(strike) ? 0 : strike * 10 === Math.round(strike * 10) ? 1 : 2
  )
}

/** The F&O exchange an address names, from either side: NFO, BFO, or the underlying's own. */
export function foExchange(text: string | undefined): 'NFO' | 'BFO' | 'MCX' | null {
  switch ((text ?? '').toUpperCase()) {
    case 'NFO':
    case 'NSE':
    case 'NSE_INDEX':
      return 'NFO'
    case 'BFO':
    case 'BSE':
    case 'BSE_INDEX':
      return 'BFO'
    case 'MCX':
      return 'MCX'
    default:
      return null
  }
}

/** `/options/NFO/NIFTY?expiry=2026-11-17&strike=25900`: the address other screens link to. */
export function optionsPath(
  exchange: string,
  underlying: string,
  at: { expiry?: string | null; strike?: number | null } = {}
): string {
  const query = new URLSearchParams()
  if (at.expiry) query.set('expiry', at.expiry)
  if (at.strike != null) query.set('strike', String(at.strike))
  const tail = query.toString()
  return `/options/${encodeURIComponent(exchange)}/${encodeURIComponent(underlying)}${tail ? `?${tail}` : ''}`
}
