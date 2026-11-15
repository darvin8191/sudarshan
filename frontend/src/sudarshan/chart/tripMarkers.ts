/**
 * SUDARSHAN M14.8 (design p7 9.7, F10): a trade from the journal on the phone chart.
 *
 * "See on chart" on a trade's sheet opens `/chart/<exchange>/<symbol>?trip=<id>`:
 * the phone chart, live as always, with the trade's fills marked at their
 * candles through the overlay seam (`PhoneChart.setOverlays({ markers })`).
 *
 * - A buy is an up-triangle under its candle with the letter B, a sell a
 *   down-triangle over its candle with the letter S, in `--buy-solid` and
 *   `--sell-solid`: a shape and a letter, never colour alone. The side decides
 *   the look, so a short's entry is an S and its exit a B. An entry carries its
 *   price beside the letter ("B 131.20"), an exit its letter only.
 * - The fills of one candle, one role and one side are one mark, at their
 *   average price.
 * - The interval comes from the trade's length: 1 minute up to 2 hours, 5
 *   minutes up to 2 days, daily beyond (an open trade's length runs to now).
 * - The view goes to the entry, with the exit in it where both fit.
 * - A trade older than the candles the chart holds at the chosen interval says
 *   so and moves to daily candles. A timeframe the owner picks himself is never
 *   overridden: there it only says so.
 *
 * Nothing here sends anything anywhere.
 */
import { formatPrice } from '@/sudarshan/dashboard/format'
import { IST_OFFSET_S, PRICE_SCALE } from '@/sudarshan/journal/types'
import type { OverlayMarker } from './overlays'
import { chartPath } from './prefs'

/** The query that names the trade on the chart's address. */
export const TRIP_QUERY = 'trip'
/** Longest trade shown on 1-minute candles, and on 5-minute ones (design p7 9.7). */
export const ONE_MINUTE_UP_TO_MS = 2 * 3_600_000
export const FIVE_MINUTES_UP_TO_MS = 2 * 86_400_000
export const DAILY = 'D'
export type TripInterval = '1m' | '5m' | typeof DAILY

export const OLDER_THAN_HISTORY = 'This trade is older than the chart’s history at this interval.'
export const SHOWN_DAILY = 'Daily candles are shown instead.'
export const PICK_LONGER = 'Pick a longer timeframe to see it.'
export const NOT_SHOWN = 'The trade’s markers could not be shown.'

/** Candles a trade's view opens with at least, at most, and from its entry when it is longer. */
export const FOCUS_MIN_BARS = 40
export const FOCUS_MAX_BARS = 200
export const FOCUS_ENTRY_BARS = 70

/** The address "See on chart" opens. */
export function tripChartPath(trip: { id: number; symbol: string; exchange: string }): string {
  return `${chartPath(trip.symbol, trip.exchange)}?${TRIP_QUERY}=${trip.id}`
}

/** The trade named in a chart address's query, or null. */
export function tripIdOf(search: string): number | null {
  const raw = new URLSearchParams(search).get(TRIP_QUERY) ?? ''
  if (!/^[1-9]\d{0,14}$/.test(raw)) return null
  return Number(raw)
}

/** The interval a trade is shown at, from its length. */
export function tripInterval(
  openedMs: number,
  closedMs: number | null,
  nowMs: number
): TripInterval {
  const length = Math.max(0, (closedMs ?? nowMs) - openedMs)
  if (length <= ONE_MINUTE_UP_TO_MS) return '1m'
  if (length <= FIVE_MINUTES_UP_TO_MS) return '5m'
  return DAILY
}

/** Candles of a day or longer are placed by the day in India, not by the clock. */
export function byDay(interval: string): boolean {
  return !/^\d+[smh]$/.test(interval)
}

const istDay = (sec: number) => Math.floor((sec + IST_OFFSET_S) / 86_400)

/** The pre-open's fills come at most this long before the day's first candle. */
export const PRE_OPEN_S = 15 * 60

/**
 * The index in `times` (candle opens, UTC seconds, oldest first) of the candle
 * `sec` falls in, or -1 when it is before the first. Intraday: the last candle
 * that opened at or before it, and a fill up to `PRE_OPEN_S` before its day's
 * first candle (the pre-open) goes to that candle. By the day: the last candle
 * of its day or before.
 */
export function barIndexOf(times: readonly number[], sec: number, daily: boolean): number {
  const key = daily ? istDay : (s: number) => s
  const target = key(sec)
  let lo = 0
  let hi = times.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (key(times[mid]) <= target) {
      found = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  if (daily) return found
  const next = found + 1
  const day = istDay(sec)
  const preOpen =
    next < times.length && istDay(times[next]) === day && times[next] - sec <= PRE_OPEN_S
  return preOpen && (found < 0 || istDay(times[found]) < day) ? next : found
}

/** What a leg of the journal's trade carries that the marks need. */
export interface MarkLeg {
  role: 'entry' | 'exit'
  action: 'BUY' | 'SELL'
  qty: number
  price_u: number
  exch_ts_ms: number
}

export interface TripMarks {
  markers: OverlayMarker[]
  /** The entry's candle (the first leg's when no entry is known); null when not in the history. */
  first: number | null
  /** The last marked candle. */
  last: number | null
  /** The entry is older than the first candle held. */
  older: boolean
}

/** The trade's marks on these candles, prices written with `decimals`. */
export function tripMarkers(
  legs: readonly MarkLeg[],
  times: readonly number[],
  daily: boolean,
  decimals: number
): TripMarks {
  const sorted = [...legs].sort((a, b) => a.exch_ts_ms - b.exch_ts_ms)
  const lead = sorted.find((leg) => leg.role === 'entry') ?? sorted[0]
  const groups = new Map<
    string,
    { role: MarkLeg['role']; action: MarkLeg['action']; index: number; qty: number; value: number }
  >()
  let first: number | null = null
  let last: number | null = null
  let older = false
  for (const leg of sorted) {
    const index = barIndexOf(times, Math.floor(leg.exch_ts_ms / 1000), daily)
    if (leg === lead) {
      if (index < 0) older = true
      else first = index
    }
    if (index < 0) continue
    last = last === null ? index : Math.max(last, index)
    const key = `${leg.role}:${leg.action}:${index}`
    const group = groups.get(key) ?? { role: leg.role, action: leg.action, index, qty: 0, value: 0 }
    group.qty += leg.qty
    group.value += leg.qty * leg.price_u
    groups.set(key, group)
  }
  const markers = [...groups.values()]
    .sort((a, b) => a.index - b.index || (a.role === b.role ? 0 : a.role === 'entry' ? -1 : 1))
    .map((group): OverlayMarker => {
      const letter = group.action === 'BUY' ? 'B' : 'S'
      const price = group.value / group.qty / PRICE_SCALE
      return {
        id: `trip:${group.role}:${group.action}:${times[group.index]}`,
        time: times[group.index],
        side: group.action,
        shape: 'triangle',
        text: group.role === 'entry' ? `${letter} ${formatPrice(price, decimals)}` : letter,
      }
    })
  return { markers, first, last, older }
}

/**
 * The candles a trade's view shows, as indexes: the entry to the last mark with
 * a margin, at least `FOCUS_MIN_BARS` wide; a trade wider than `FOCUS_MAX_BARS`
 * opens on its entry at the chart's usual width. Kept within the candles held,
 * with a little room after the last.
 */
export function focusRange(
  first: number,
  last: number,
  count: number
): { from: number; to: number } {
  const span = Math.max(0, last - first)
  const pad = Math.max(6, Math.ceil(span * 0.1))
  let from = first - pad
  let to = Math.max(first, last) + pad
  if (to - from > FOCUS_MAX_BARS) {
    from = first - 6
    to = from + FOCUS_ENTRY_BARS
  } else if (to - from < FOCUS_MIN_BARS) {
    const middle = (first + Math.max(first, last)) / 2
    from = Math.floor(middle - FOCUS_MIN_BARS / 2)
    to = from + FOCUS_MIN_BARS
  }
  const room = count + 2
  if (to > room) {
    from -= to - room
    to = room
  }
  if (from < 0) {
    to -= from
    from = 0
  }
  return { from, to }
}
