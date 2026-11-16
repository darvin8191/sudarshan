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
 * The trade is read once, through the journal's existing `trip_get`. Nothing
 * here sends anything anywhere.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { formatPrice } from '@/sudarshan/dashboard/format'
import { journalCall, problemOf, type TripDetail } from '@/sudarshan/journal/api'
import { IST_OFFSET_S, PRICE_SCALE, type TripLeg } from '@/sudarshan/journal/types'
import type { OverlayMarker, Overlays } from './overlays'
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
/** Room kept beside the trade's marks, so an entry's price is never cut at the edge. */
export const FOCUS_MARGIN_BARS = 8

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
 * a margin, at least `FOCUS_MIN_BARS` wide where the candles held allow it; a
 * trade wider than `FOCUS_MAX_BARS` opens on its entry at the chart's usual
 * width. Kept to the candles held, with a little room after the last and before
 * the first (where the chart reads older candles in), never padded wider.
 */
export function focusRange(
  first: number,
  last: number,
  count: number
): { from: number; to: number } {
  const end = Math.max(first, last)
  const pad = Math.max(FOCUS_MARGIN_BARS, Math.ceil((end - first) * 0.1))
  let from = first - pad
  let to = end + pad
  if (to - from > FOCUS_MAX_BARS) {
    from = first - FOCUS_MARGIN_BARS
    to = from + FOCUS_ENTRY_BARS
  } else if (to - from < FOCUS_MIN_BARS) {
    from = Math.floor((first + end) / 2 - FOCUS_MIN_BARS / 2)
    to = from + FOCUS_MIN_BARS
  }
  const room = count + 2
  const floor = -FOCUS_MARGIN_BARS
  if (to > room) {
    from -= to - room
    to = room
  }
  if (from < floor) {
    to = Math.min(room, to + floor - from)
    from = floor
  }
  return { from, to }
}

/** What of the phone chart a trade's view uses (`PhoneChart` has each). */
export interface TripChart {
  setOverlays(next: Overlays): void
  barTimes(): number[]
  showBars(range: { from: number; to: number }): void
  onLoaded(listener: (interval: string) => void): () => void
}

type NoticeKind = 'daily' | 'picked' | 'failed'

/** The trade sheet's answer: `trip_get` sends each leg with its role (the contract's `legs`). */
type TripWithLegs = TripDetail & { legs?: TripLeg[] }

/**
 * The trade named in the chart's address, on the chart: read once, its
 * interval chosen, its marks laid on every load and the view placed on it.
 * `notice` is a sentence for the owner, or null.
 */
export function useTripMarkers({
  owner,
  tripId,
  symbol,
  exchange,
  decimals,
  setTimeframe,
}: {
  owner: TripChart | null
  tripId: number | null
  symbol: string
  exchange: string
  decimals: number
  setTimeframe: (interval: string) => void
}): { notice: string | null; dismiss: () => void } {
  const [notice, setNotice] = useState<{ kind: NoticeKind; text: string } | null>(null)
  const trip = useRef<TripWithLegs | null>(null)
  /** The interval chosen for the trade; one the owner picks himself is never overridden. */
  const chosen = useRef<string | null>(null)
  /** The interval of the last load that drew its candles. */
  const loaded = useRef<string | null>(null)
  const now = useRef({ owner, decimals, setTimeframe })
  now.current = { owner, decimals, setTimeframe }

  const apply = useCallback((interval: string, place: boolean) => {
    const { owner: chart, decimals: dp, setTimeframe: pick } = now.current
    const found = trip.current
    if (!chart || !found) return
    const times = chart.barTimes()
    if (!times.length) return
    const daily = byDay(interval)
    const marks = tripMarkers(found.legs ?? [], times, daily, dp)
    chart.setOverlays({ markers: marks.markers })
    if (!place) return
    if (marks.older) {
      if (!daily && interval === chosen.current) {
        chosen.current = DAILY
        setNotice({ kind: 'daily', text: `${OLDER_THAN_HISTORY} ${SHOWN_DAILY}` })
        pick(DAILY)
      } else {
        const text = daily ? OLDER_THAN_HISTORY : `${OLDER_THAN_HISTORY} ${PICK_LONGER}`
        setNotice({ kind: 'picked', text })
      }
      return
    }
    // The trade is in view: a sentence about another timeframe no longer holds.
    setNotice((was) =>
      was?.kind === 'picked' || (was?.kind === 'daily' && interval !== chosen.current) ? null : was
    )
    if (marks.first !== null && marks.last !== null)
      chart.showBars(focusRange(marks.first, marks.last, times.length))
  }, [])

  // Read once per trade; its interval is chosen from its length.
  useEffect(() => {
    trip.current = null
    chosen.current = null
    setNotice(null)
    if (tripId === null) return
    const control = new AbortController()
    let alive = true
    void journalCall('trip_get', { id: tripId }, control.signal).then(
      (result) => {
        if (!alive) return
        if (!result.enabled) {
          setNotice({ kind: 'failed', text: `${NOT_SHOWN} ${result.message}` })
          return
        }
        const found = result.data
        // A link to another instrument's trade marks nothing here.
        if (found.symbol !== symbol || found.exchange !== exchange) return
        trip.current = found
        const interval = tripInterval(found.opened_ms, found.closed_ms, Date.now())
        chosen.current = interval
        now.current.setTimeframe(interval)
        if (loaded.current === interval) apply(interval, true)
      },
      (error: unknown) => {
        if (alive) setNotice({ kind: 'failed', text: `${NOT_SHOWN} ${problemOf(error).message}` })
      }
    )
    return () => {
      alive = false
      control.abort()
    }
  }, [tripId, symbol, exchange, apply])

  // Every load that draws its candles lays the marks again, and places the view.
  useEffect(() => {
    if (!owner || tripId === null) return
    loaded.current = null
    return owner.onLoaded((interval) => {
      loaded.current = interval
      apply(interval, true)
    })
  }, [owner, tripId, apply])

  // The instrument's tick arrived after the marks: their prices are written again.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the decimals are the trigger
  useEffect(() => {
    if (loaded.current) apply(loaded.current, false)
  }, [decimals, apply])

  const dismiss = useCallback(() => setNotice(null), [])
  return { notice: notice?.text ?? null, dismiss }
}
