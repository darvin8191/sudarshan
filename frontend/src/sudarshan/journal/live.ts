/**
 * The journal's live arithmetic and words, free of React so every rule is a plain function a test
 * calls (the L vectors of sudarshan/journal/tests/vectors/metrics.json are its tests).
 *
 * An open trade's P&L is (last price - entry average) x open quantity x side. A price is
 * `price_u` (rupees x 10^4) and money is paise, so the product is `qty x diff_u / 100`, divided
 * once and rounded half away from zero (a loss mirrors a gain), in BigInt: the same rule the
 * service and the portfolio use (portfolio/money.ts `roundDiv`).
 */
import type { SymbolData } from '@/lib/MarketDataManager'
import { type Price, priceOf } from '@/sudarshan/dashboard/live'
import { roundDiv } from '@/sudarshan/portfolio/money'
import { PRICE_SCALE, type Side, type Trip } from './types'

/** Rupees x 10^4 for paise: `price_u x qty / 100` is paise. */
const PRICE_U_PER_PAISE = PRICE_SCALE / 100

/** +1 for a long, -1 for a short. */
export const sideSign = (side: Side): 1 | -1 => (side === 'long' ? 1 : -1)

/** The quantity still open: what the entries bought or sold less what the exits closed. */
export function openQty(trip: Pick<Trip, 'entry_qty' | 'exit_qty'>): number {
  return Math.max(0, trip.entry_qty - trip.exit_qty)
}

/**
 * Whether a position not in the figures is a trade the journal saw opened and did not see closed
 * (the service's `pairing.unclosed`): it ended holding more than its fills took off, because the
 * account no longer held it when the journal counted again. A position opened before the journal
 * ends with at least as much sold as the journal saw bought.
 */
export function closingNotSeen(
  trip: Pick<Trip, 'status' | 'closed_ms' | 'entry_qty' | 'exit_qty'>
): boolean {
  return trip.status === 'unmatched' && trip.closed_ms !== null && trip.entry_qty > trip.exit_qty
}

/** The P&L of `qty` open at `entryAvgU` when the last price is `ltpU`, in paise. */
export function livePnlPaise(side: Side, qty: number, entryAvgU: number, ltpU: number): number {
  const moved = BigInt(ltpU - entryAvgU) * BigInt(qty) * BigInt(sideSign(side))
  const paise = Number(roundDiv(moved, BigInt(PRICE_U_PER_PAISE)))
  return paise === 0 ? 0 : paise
}

/** A price from the feed (a float in rupees) as `price_u`; null when it is no price. */
export function priceU(rupees: number): number | null {
  if (!Number.isFinite(rupees) || !(rupees > 0)) return null
  return Math.round(rupees * PRICE_SCALE)
}

/** The newest price in a cache line as `price_u`, when it arrived and whether a tick brought it. */
export interface JournalTick {
  ltpU: number
  at: number
  live: boolean
}

export function tickOf(line: SymbolData | undefined): JournalTick | null {
  const price: Price | null = priceOf(line)
  if (!price) return null
  const ltpU = priceU(price.ltp)
  return ltpU === null ? null : { ltpU, at: price.at, live: price.live }
}

const COUNT = new Intl.NumberFormat('en-IN')

/** A quantity or a count as the owner reads it: 1,300, 12,34,567 (review L1). */
export const countText = (n: number): string => COUNT.format(n)

/** A price in rupees as the owner reads it: 2 places, 4 for a currency tick that needs them. */
export function priceText(u: number): string {
  const places = u % 100 === 0 ? 2 : 4
  return new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  }).format(u / PRICE_SCALE)
}

/** "5 min", "2 h 14 min", "3 d 4 h": how long a trade was or has been held. */
export function holdWords(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  if (total < 60) return `${total} s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return minutes % 60 === 0 ? `${hours} h` : `${hours} h ${minutes % 60} min`
  const days = Math.floor(hours / 24)
  return hours % 24 === 0 ? `${days} d` : `${days} d ${hours % 24} h`
}

const DAY_NAME = new Intl.DateTimeFormat('en-IN', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

/** 20261113 as "Fri 13 Nov 2026". */
export function dayWords(day: number): string {
  const year = Math.floor(day / 10000)
  const month = Math.floor(day / 100) % 100
  const date = day % 100
  return DAY_NAME.format(new Date(Date.UTC(year, month - 1, date))).replace(/,/g, '')
}

export interface DayGroup<T extends Trip = Trip> {
  day: number
  trips: T[]
  /** The sum of the nets the day's trades have; null when none of them has one yet. */
  net_paise: number | null
  /** The day's trades with no net yet: the sum leaves them out, and says so (review M7). */
  missing: number
  /** Whether a net in the sum rests on estimated charges, as a card's "est." says. */
  estimated: boolean
}

/** Closed trips by the day they closed, newest day first and newest trade first within it. */
export function groupByDay<T extends Trip>(trips: readonly T[]): DayGroup<T>[] {
  const by = new Map<number, T[]>()
  for (const trip of trips) {
    const day = trip.close_day ?? trip.open_day
    by.set(day, [...(by.get(day) ?? []), trip])
  }
  return [...by.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([day, list]) => {
      const sorted = [...list].sort(
        (a, b) => (b.closed_ms ?? 0) - (a.closed_ms ?? 0) || b.id - a.id
      )
      const priced = sorted.filter((t) => t.net_paise !== null)
      return {
        day,
        trips: sorted,
        net_paise: priced.length ? priced.reduce((sum, t) => sum + (t.net_paise ?? 0), 0) : null,
        missing: sorted.length - priced.length,
        estimated: priced.some(
          (t) => t.charges_actual_paise === null && t.charges_modelled !== false
        ),
      }
    })
}

/** A trade's strategy as the owner sees it: their own label when they set one. */
export const strategyOf = (trip: Pick<Trip, 'strategy' | 'strategy_override'>): string =>
  (trip.strategy_override ?? trip.strategy).trim()

/** Win, loss or flat, by the net as shown. */
export type Outcome = 'profit' | 'loss' | 'flat'
export const outcomeOf = (net: number): Outcome => (net > 0 ? 'profit' : net < 0 ? 'loss' : 'flat')

export interface ExcursionBar {
  /** 0 to 100, where the worst point and the best point sit on a bar scaled to both. */
  netAt: number
  zeroAt: number
}

/**
 * The small bar of how far a trade went against it (MAE) and for it (MFE), with where it was
 * left (the net). Null when the excursion was not computed.
 */
export function excursionBar(
  trip: Pick<Trip, 'mae_paise' | 'mfe_paise' | 'net_paise'>
): ExcursionBar | null {
  const { mae_paise: mae, mfe_paise: mfe, net_paise: net } = trip
  if (mae === null || mfe === null || net === null) return null
  const low = Math.min(mae, net, 0)
  const high = Math.max(mfe, net, 0)
  const span = high - low
  if (span === 0) return null
  const at = (value: number) => ((value - low) / span) * 100
  return { netAt: at(net), zeroAt: at(0) }
}
