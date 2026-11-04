/**
 * How old a price is, and what to say about it: the phone's copy of core/freshness.py (M7.39).
 *
 * `today` is the IST day now and `lastSessionDay` the latest trading day whose session has begun;
 * the service works both out, because the phone knows neither the exchange's calendar nor (here)
 * the clock. Runs the vectors in tests/vectors/freshness.json.
 */
import { businessDaysBetween, clockOfInstant, daysBetween } from './days'
import { type Freshness, MANUAL_OLD_DAYS, NAV_OLD_BUSINESS_DAYS, type PriceInfo } from './types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DOT = ' · '
const MANUAL_OLD_DAYS_MIN = 7
const MANUAL_OLD_DAYS_MAX = 365
export const NO_PRICE = 'No price yet'

/** 12 Sep, and 12 Sep 2025 when it is not in the year of `near`. */
export function when(day: number, near: number): string {
  const text = `${day % 100} ${MONTHS[(Math.floor(day / 100) % 100) - 1]}`
  return Math.floor(day / 10_000) === Math.floor(near / 10_000)
    ? text
    : `${text} ${Math.floor(day / 10_000)}`
}

/** A fund's NAV dated `navDay`. */
export function navFreshness(
  navDay: number,
  lastSessionDay: number,
  holidays: readonly number[] = []
): Freshness {
  const words = `NAV of ${when(navDay, lastSessionDay)}`
  if (businessDaysBetween(navDay, lastSessionDay, holidays) > NAV_OLD_BUSINESS_DAYS) {
    return { state: 'old', words: `${words}; AMFI has not published a newer one.` }
  }
  return { state: 'fresh', words }
}

/** A value kept by hand, dated `priceDay`; `noun` is "Balance" for a loan. */
export function manualFreshness(
  priceDay: number,
  today: number,
  oldDays: number = MANUAL_OLD_DAYS,
  noun = 'Value'
): Freshness {
  const age = Math.max(0, daysBetween(priceDay, today))
  const limit = Math.min(Math.max(oldDays, MANUAL_OLD_DAYS_MIN), MANUAL_OLD_DAYS_MAX)
  const howOld = age === 0 ? 'today' : `${age} day${age === 1 ? '' : 's'} old`
  return {
    state: age > limit ? 'old' : 'fresh',
    words: `${noun} from ${when(priceDay, today)}${DOT}${howOld}`,
  }
}

function listedFreshness(price: PriceInfo, today: number, lastSessionDay: number): Freshness {
  const day = price.day as number
  if (day < lastSessionDay) {
    return { state: 'old', words: `Price of ${when(day, today)}; no newer price yet.` }
  }
  if (price.basis === 'close') return { state: 'fresh', words: `Close of ${when(day, today)}` }
  let what: string
  if (price.source === 'quote') what = 'quote'
  else if (price.late) what = 'closing record, taken late'
  else if (day < today) what = 'closing record'
  else return { state: 'fresh', words: null }
  const stamp = [
    ...(price.at !== null ? [clockOfInstant(price.at)] : []),
    ...(day !== today ? [when(day, today)] : []),
  ].join(', ')
  return { state: 'fresh', words: stamp ? `Last price ${stamp} (${what})` : `Last price (${what})` }
}

/** How fresh a `PriceInfo` is, by what kind of price it is (its `basis`). */
export function priceFreshness(
  price: PriceInfo,
  options: {
    today: number
    lastSessionDay: number
    oldDays?: number
    holidays?: readonly number[]
    noun?: string
  }
): Freshness {
  if (price.price_u === null || price.day === null) return { state: 'old', words: NO_PRICE }
  if (price.basis === 'nav')
    return navFreshness(price.day, options.lastSessionDay, options.holidays)
  if (price.basis === 'manual') {
    return manualFreshness(price.day, options.today, options.oldDays, options.noun)
  }
  return listedFreshness(price, options.today, options.lastSessionDay)
}
