/**
 * The closed trades the Trades part holds, read in whole days (review H1 of v0.11.0) and kept
 * across re-reads (review M2).
 *
 * - A page is the newest closed trades up to a page's worth; when it is full, its last day is read
 *   again by itself (the service's `from` and `to` are the close day) so no day is shown with only
 *   some of its trades. A day too large to finish in `DAY_READS` reads is named as partly read.
 * - "Show earlier trades" asks for the days before the oldest one held (`to` the day before it),
 *   finished the same way.
 * - A re-read of the newest page is the truth for the days it covers: a trade gone from them is
 *   gone, a new one is added. Every earlier day held is kept as it was, so a trade pushed from the
 *   first page into the second by a new close stays on screen.
 *
 * Plain functions over the service's answers; the hook in TripsList.tsx holds the result.
 */
import { journalData } from './api'
import type { Mode, Trip } from './types'

/** A trade as the list reads it: the contract's row, and the tags the service may name on it. */
export type TripRow = Trip & { mistakes?: number[] }

/** A page of closed trades. */
export const CLOSED_PAGE = 100
/** The most the service answers in one read (trip_ops LIMIT_MAX): a day is finished in these. */
export const DAY_PAGE = 500
/** The reads a day may take before it is shown as partly read (10,000 trades at most). */
export const DAY_READS = 20

export interface TripsRead {
  open: TripRow[]
  /** Whole days, newest first by close and then id, as the service orders them. */
  closed: TripRow[]
  /** Whether there may be days before the oldest one held. */
  more: boolean
  /** Days that could not be read whole: their count says "so far" and they show no total. */
  partial: number[]
}

/** The day a closed trade belongs to, as the service pages it (close day, else the opening's). */
export const dayOf = (trip: Pick<Trip, 'close_day' | 'open_day'>): number =>
  trip.close_day ?? trip.open_day

/** Newest first by close, then by id: the service's order. */
export const byClose = (a: Trip, b: Trip): number =>
  (b.closed_ms ?? 0) - (a.closed_ms ?? 0) || b.id - a.id

/** A page read, finished on a whole day. `full`: the service had at least a page to give. */
export interface ClosedPage {
  rows: TripRow[]
  full: boolean
  partial: number[]
}

/** Every closed trade of `day`, over what is already held of it. */
async function wholeDay(
  mode: Mode,
  day: number,
  held: readonly TripRow[]
): Promise<{ rows: TripRow[]; whole: boolean }> {
  const byId = new Map(held.map((t) => [t.id, t]))
  let before: number | undefined
  for (let read = 0; read < DAY_READS; read += 1) {
    const page = (await journalData('trips', {
      mode,
      status: 'closed',
      from: day,
      to: day,
      limit: DAY_PAGE,
      ...(before === undefined ? {} : { before }),
    })) as TripRow[]
    let fresh = 0
    for (const trip of page) {
      if (!byId.has(trip.id)) fresh += 1
      byId.set(trip.id, trip)
    }
    if (page.length < DAY_PAGE) return { rows: [...byId.values()], whole: true }
    // Up to and including the last one's close: several can close in the same millisecond.
    const last = page.at(-1)
    if (fresh === 0 || !last || last.closed_ms === null) break
    before = last.closed_ms + 1
  }
  return { rows: [...byId.values()], whole: false }
}

/** The newest page of closed trades (`to` absent), or the one ending on day `to`, whole days. */
export async function readClosedPage(mode: Mode, to?: number): Promise<ClosedPage> {
  const page = (await journalData('trips', {
    mode,
    status: 'closed',
    limit: CLOSED_PAGE,
    ...(to === undefined ? {} : { to }),
  })) as TripRow[]
  const full = page.length >= CLOSED_PAGE
  const last = page.at(-1)
  if (!full || !last) return { rows: [...page].sort(byClose), full, partial: [] }
  const day = dayOf(last)
  const rest = await wholeDay(
    mode,
    day,
    page.filter((t) => dayOf(t) === day)
  )
  return {
    rows: [...page.filter((t) => dayOf(t) !== day), ...rest.rows].sort(byClose),
    full,
    partial: rest.whole ? [] : [day],
  }
}

/** The newest page, read again, laid over what is held (`held`, from the cache), and the open. */
export function overNewest(
  held: TripsRead | undefined,
  open: TripRow[],
  newest: ClosedPage
): TripsRead {
  if (!newest.full) {
    // The page held everything there is: whatever else was held is gone.
    return { open, closed: newest.rows, more: false, partial: newest.partial }
  }
  const oldest = newest.rows.at(-1)
  const edge = oldest ? dayOf(oldest) : Number.POSITIVE_INFINITY
  const ids = new Set(newest.rows.map((t) => t.id))
  const kept = (held?.closed ?? []).filter((t) => dayOf(t) < edge && !ids.has(t.id))
  return {
    open,
    closed: [...newest.rows, ...kept].sort(byClose),
    more: kept.length > 0 ? (held?.more ?? true) : true,
    partial: [...newest.partial, ...(held?.partial ?? []).filter((day) => day < edge)],
  }
}

/** An earlier page added after the days held; what it repeats is left out. */
export function withEarlier(held: TripsRead, page: ClosedPage): TripsRead {
  const oldest = held.closed.at(-1)
  const edge = oldest ? dayOf(oldest) : Number.POSITIVE_INFINITY
  const ids = new Set(held.closed.map((t) => t.id))
  const fresh = page.rows.filter((t) => dayOf(t) < edge && !ids.has(t.id))
  return {
    ...held,
    closed: [...held.closed, ...fresh].sort(byClose),
    more: page.full && fresh.length > 0,
    partial: [...held.partial, ...page.partial],
  }
}

/** The day before the oldest day held: where "Show earlier trades" asks the next page to end. */
export function dayBefore(held: TripsRead): number | null {
  const oldest = held.closed.at(-1)
  // YYYYMMDD less one is below that day and above every earlier one: no calendar needed.
  return oldest ? dayOf(oldest) - 1 : null
}
