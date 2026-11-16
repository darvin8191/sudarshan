/**
 * SUDARSHAN M14.8 (design p7 9.7, F10): a trade from the journal on the phone chart.
 *
 * "See on chart" on a trade's sheet opens `/chart/<exchange>/<symbol>?trip=<id>`:
 * the phone chart, live as always, with the trade's fills marked at their
 * candles through the overlay seam (`PhoneChart.setOverlays({ markers })`).
 *
 * - A buy is a tag under its candle with the letter B and a point up at it, a
 *   sell a tag over its candle with the letter S and a point down, in
 *   `--buy-solid` and `--sell-solid`, at one size whatever the zoom: a shape
 *   and a letter, never colour alone. The side decides the look, so a short's
 *   entry is an S and its exit a B. An entry carries its price beside the
 *   letter ("B 131.20") where a candle is wide enough to hold it; a tap on any
 *   mark opens its fills in full.
 * - The fills of one candle, one role and one side are one mark, at their
 *   average price.
 * - A fill is marked on its own candle only. Until that candle is held (the
 *   day's first has not loaded, or the history skips its day) it is left
 *   unmarked, and a sentence says how many are.
 * - The interval comes from the trade's length: 1 minute up to 2 hours, 5
 *   minutes up to 2 days, daily beyond (an open trade's length runs to now).
 * - The view goes to the entry, with the exit in it where both fit.
 * - A trade older than the candles the chart holds at the chosen interval says
 *   so and moves to daily candles. A timeframe the owner picks himself is never
 *   overridden: there it only says so.
 *
 * The trade is read through the journal's existing `trip_get` when the chart
 * opens, and again when the app hears an order of this instrument change, the
 * app's connection comes back or the page is returned to: never on a timer.
 * When the address stops naming the trade, its marks and its timeframe go.
 * Nothing here sends anything anywhere.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { istTime } from '@/sudarshan/books/time'
import { formatPrice } from '@/sudarshan/dashboard/format'
import { journalCall, problemOf, type TripDetail } from '@/sudarshan/journal/api'
import { IST_OFFSET_S, PRICE_SCALE, type TripLeg } from '@/sudarshan/journal/types'
import { AFTER_ORDER_MS, AWAY_MS } from '@/sudarshan/journal/useJournalSync'
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
/** Fills whose own candle the chart does not hold, in words. */
export function outsideWords(count: number): string {
  return count === 1
    ? '1 fill is outside the loaded candles.'
    : `${count} fills are outside the loaded candles.`
}
/** The marks could not be read again: how old they are, and when they are read next. */
export function marksAsOf(readMs: number): string {
  return `Marks as of ${istTime(readMs).slice(0, 5)}: the journal did not answer. They are read again at the next order update.`
}

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

/** What every trade mark's id starts with. */
export const TRIP_MARK = 'trip:'

/** The pre-open's fills come at most this long before the day's first candle. */
export const PRE_OPEN_S = 15 * 60

/** `barIndexOf`'s answer for a fill whose own candle is not among those held. */
export const NOT_HELD = -2

/**
 * The index in `times` (candle opens, UTC seconds, oldest first) of the candle
 * `sec` falls in; -1 when it is before the first; `NOT_HELD` when the candle
 * it would land on belongs to another day in India, so its own is not held (it
 * has not loaded yet, or the history skips that day). Intraday: the last
 * candle that opened at or before it, and a fill up to `PRE_OPEN_S` before its
 * day's first candle (the pre-open) goes to that candle. By the day: the candle
 * of its day. `spans` is for candles of a week or a month, which hold many
 * days: there the last candle at or before the fill's day is its own.
 */
export function barIndexOf(
  times: readonly number[],
  sec: number,
  daily: boolean,
  spans = false
): number {
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
  const day = istDay(sec)
  if (!daily) {
    const next = found + 1
    const preOpen =
      next < times.length && istDay(times[next]) === day && times[next] - sec <= PRE_OPEN_S
    if (preOpen && (found < 0 || istDay(times[found]) < day)) return next
  }
  if (found < 0 || spans) return found
  return istDay(times[found]) === day ? found : NOT_HELD
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
  /** Fills left unmarked because their own candle is not held. */
  outside: number
  /** The fills each mark stands for, by the mark's id, oldest first. */
  fills: Map<string, MarkLeg[]>
}

/** The trade's marks on these candles, prices written with `decimals`. */
export function tripMarkers(
  legs: readonly MarkLeg[],
  times: readonly number[],
  daily: boolean,
  decimals: number,
  spans = false
): TripMarks {
  const sorted = [...legs].sort((a, b) => a.exch_ts_ms - b.exch_ts_ms)
  const lead = sorted.find((leg) => leg.role === 'entry') ?? sorted[0]
  const groups = new Map<
    string,
    {
      role: MarkLeg['role']
      action: MarkLeg['action']
      index: number
      qty: number
      value: number
      legs: MarkLeg[]
    }
  >()
  let first: number | null = null
  let last: number | null = null
  let older = false
  let outside = 0
  for (const leg of sorted) {
    const index = barIndexOf(times, Math.floor(leg.exch_ts_ms / 1000), daily, spans)
    if (index === NOT_HELD) outside += 1
    if (leg === lead) {
      if (index === -1) older = true
      else if (index >= 0) first = index
    }
    if (index < 0) continue
    last = last === null ? index : Math.max(last, index)
    const key = `${leg.role}:${leg.action}:${index}`
    const group = groups.get(key) ?? {
      role: leg.role,
      action: leg.action,
      index,
      qty: 0,
      value: 0,
      legs: [],
    }
    group.qty += leg.qty
    group.value += leg.qty * leg.price_u
    group.legs.push(leg)
    groups.set(key, group)
  }
  const fills = new Map<string, MarkLeg[]>()
  const markers = [...groups.values()]
    .sort((a, b) => a.index - b.index || (a.role === b.role ? 0 : a.role === 'entry' ? -1 : 1))
    .map((group): OverlayMarker => {
      const letter = group.action === 'BUY' ? 'B' : 'S'
      const price = group.value / group.qty / PRICE_SCALE
      const id = `${TRIP_MARK}${group.role}:${group.action}:${times[group.index]}`
      fills.set(id, group.legs)
      return {
        id,
        time: times[group.index],
        side: group.action,
        shape: 'tag',
        text: group.role === 'entry' ? `${letter} ${formatPrice(price, decimals)}` : letter,
        short: letter,
      }
    })
  return { markers, first, last, older, outside, fills }
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
  /** Hear when the candles held changed: a new one opened, or older ones were read in. */
  onBars?(listener: () => void): () => void
}

type NoticeKind = 'daily' | 'picked' | 'failed'

/** The trade sheet's answer: `trip_get` sends each leg with its role (the contract's `legs`). */
type TripWithLegs = TripDetail & { legs?: TripLeg[] }

/** What the app's order events carry that names the instrument (live and analyzer mode). */
interface OrderFrame {
  symbol?: unknown
  exchange?: unknown
  request?: { symbol?: unknown; exchange?: unknown } | null
}

/** Whether an order event is about this instrument; one that names none is taken as about it. */
export function frameIsFor(frame: OrderFrame | null | undefined, symbol: string, exchange: string) {
  const named = frame?.request && typeof frame.request === 'object' ? frame.request : frame
  if (typeof named?.symbol !== 'string' || !named.symbol) return true
  return (
    named.symbol === symbol && (typeof named.exchange !== 'string' || named.exchange === exchange)
  )
}

const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'

/**
 * The trade named in the chart's address, on the chart: read when it opens and
 * again as its orders change, its interval chosen, its marks laid on every
 * load and every new candle, and the view placed on it once a load.
 * `notice` is a sentence for the owner, or null; `fillsOf` is what a mark
 * stands for, for the sheet a tap on it opens.
 */
export function useTripMarkers({
  owner,
  tripId,
  symbol,
  exchange,
  decimals,
  setTimeframe,
  restoreTimeframe,
}: {
  owner: TripChart | null
  tripId: number | null
  symbol: string
  exchange: string
  decimals: number
  setTimeframe: (interval: string) => void
  /** Put the owner's own timeframe back, when the address stops naming the trade. */
  restoreTimeframe?: () => void
}): { notice: string | null; dismiss: () => void; fillsOf: (id: string) => MarkLeg[] | null } {
  const { socket } = useSocketContext()
  const [notice, setNotice] = useState<{ kind: NoticeKind; text: string } | null>(null)
  /** Fills left unmarked, and the sentence of a read that failed after the first. */
  const [outside, setOutside] = useState(0)
  const [asOf, setAsOf] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const trip = useRef<TripWithLegs | null>(null)
  const readAt = useRef(0)
  const fills = useRef(new Map<string, MarkLeg[]>())
  /** Marks of a trade are on the chart. */
  const laid = useRef(false)
  /** The interval chosen for the trade; one the owner picks himself is never overridden. */
  const chosen = useRef<string | null>(null)
  /** The interval of the last load that drew its candles. */
  const loaded = useRef<string | null>(null)
  /** Reads the trade again, while one is named. */
  const again = useRef<(() => void) | null>(null)
  const now = useRef({ owner, decimals, setTimeframe, restoreTimeframe })
  now.current = { owner, decimals, setTimeframe, restoreTimeframe }

  const apply = useCallback((interval: string, place: boolean) => {
    const { owner: chart, decimals: dp, setTimeframe: pick } = now.current
    const found = trip.current
    if (!chart || !found) return
    const times = chart.barTimes()
    if (!times.length) return
    const daily = byDay(interval)
    const marks = tripMarkers(found.legs ?? [], times, daily, dp, daily && interval !== DAILY)
    chart.setOverlays({ markers: marks.markers })
    laid.current = true
    fills.current = marks.fills
    setOutside(marks.outside)
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

  // Read when the trade is named; its interval is chosen from its length. Read again, marks
  // only, when an order of this instrument changes, the app's connection comes back or the
  // page is returned to (the journal records a fill a moment after the broker reports it).
  useEffect(() => {
    // The marks and the timeframe of the trade shown before go with it (review L6).
    if (laid.current) now.current.owner?.setOverlays({ markers: [] })
    if (tripId === null && chosen.current !== null && loaded.current === chosen.current)
      now.current.restoreTimeframe?.()
    laid.current = false
    fills.current = new Map()
    trip.current = null
    chosen.current = null
    setNotice(null)
    setOutside(0)
    setAsOf(null)
    setDismissed(null)
    if (tripId === null) return
    const control = new AbortController()
    let alive = true
    let reading = false
    let queued = false
    const read = async (first: boolean): Promise<void> => {
      if (reading) {
        queued = true
        return
      }
      reading = true
      try {
        const result = await journalCall('trip_get', { id: tripId }, control.signal)
        if (!alive) return
        if (!result.enabled) {
          if (first) setNotice({ kind: 'failed', text: `${NOT_SHOWN} ${result.message}` })
          else if (trip.current) setAsOf(marksAsOf(readAt.current))
          return
        }
        const found = result.data
        // A link to another instrument's trade marks nothing here.
        if (found.symbol !== symbol || found.exchange !== exchange) return
        const before = trip.current
        trip.current = found
        readAt.current = Date.now()
        setAsOf(null)
        if (!before) {
          setNotice((was) => (was?.kind === 'failed' ? null : was))
          const interval = tripInterval(found.opened_ms, found.closed_ms, Date.now())
          chosen.current = interval
          now.current.setTimeframe(interval)
          if (loaded.current === interval) apply(interval, true)
        } else if (loaded.current) apply(loaded.current, false)
      } catch (error) {
        if (!alive) return
        if (!trip.current) {
          if (first) setNotice({ kind: 'failed', text: `${NOT_SHOWN} ${problemOf(error).message}` })
        } else setAsOf(marksAsOf(readAt.current))
      } finally {
        reading = false
        if (alive && queued) {
          queued = false
          void read(false)
        }
      }
    }
    void read(true)
    again.current = () => void read(false)
    return () => {
      alive = false
      again.current = null
      control.abort()
    }
  }, [tripId, symbol, exchange, apply])

  // What asks for the trade again: an order event of this instrument (after the moment the
  // journal takes to record the fill), the app's connection coming back, and a return to the
  // page that missed either or was away a while. A hidden page reads nothing. Never a timer.
  useEffect(() => {
    if (tripId === null) return
    let missed = false
    let settle: ReturnType<typeof setTimeout> | null = null
    const onOrder = (frame?: OrderFrame | null) => {
      if (!frameIsFor(frame, symbol, exchange)) return
      if (hidden()) {
        missed = true
        return
      }
      if (settle) clearTimeout(settle)
      settle = setTimeout(() => {
        settle = null
        again.current?.()
      }, AFTER_ORDER_MS)
    }
    let connectedBefore = socket?.connected === true
    const onConnect = () => {
      const back = connectedBefore
      connectedBefore = true
      if (!back) return
      if (hidden()) missed = true
      else again.current?.()
    }
    socket?.on('order_update', onOrder)
    socket?.on('analyzer_update', onOrder)
    socket?.on('connect', onConnect)
    let hiddenAt: number | null = hidden() ? Date.now() : null
    const onVisibility = () => {
      if (hidden()) {
        hiddenAt ??= Date.now()
        return
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt
      hiddenAt = null
      if (!missed && away <= AWAY_MS) return
      missed = false
      again.current?.()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      if (settle) clearTimeout(settle)
      socket?.off('order_update', onOrder)
      socket?.off('analyzer_update', onOrder)
      socket?.off('connect', onConnect)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [socket, tripId, symbol, exchange])

  // Every load that draws its candles lays the marks again, and places the view; a new candle
  // (or older ones read in) lays them again where they are, so a fill finds its own candle.
  useEffect(() => {
    if (!owner || tripId === null) return
    loaded.current = null
    const offLoaded = owner.onLoaded((interval) => {
      loaded.current = interval
      apply(interval, true)
    })
    const offBars = owner.onBars?.(() => {
      if (loaded.current) apply(loaded.current, false)
    })
    return () => {
      offLoaded()
      offBars?.()
    }
  }, [owner, tripId, apply])

  // The instrument's tick arrived after the marks: their prices are written again.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the decimals are the trigger
  useEffect(() => {
    if (loaded.current) apply(loaded.current, false)
  }, [decimals, apply])

  const text =
    [notice?.text, outside > 0 ? outsideWords(outside) : null, asOf].filter(Boolean).join(' ') ||
    null
  // A sentence that went away is new when it comes back.
  useEffect(() => {
    if (text === null) setDismissed(null)
  }, [text])
  const dismiss = useCallback(() => setDismissed(text), [text])
  const fillsOf = useCallback((id: string) => fills.current.get(id) ?? null, [])
  return { notice: text === dismissed ? null : text, dismiss, fillsOf }
}
