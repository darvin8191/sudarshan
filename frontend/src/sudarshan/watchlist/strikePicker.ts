import type { Expiry, ExpiryRule } from './v2/types'

/** The strikes asked for each side of the ATM (the picker shows ATM plus or minus this many). */
export const EACH_SIDE = 10

/** A move past the midpoint asks the server again at most once in this long (a turned-away move waits for it). */
export const ASK_GAP_MS = 2000

/** The underlying's place among the live items, so its price is read once like a row's (negative: never an OpenAlgo item). */
export const PICKER_ITEM = -2_000_000_000

/**
 * The strike nearest the price, a tie to the lower (the server's rule, W14). The strikes need not be
 * sorted. Null when there are none or the price is not a number above zero.
 */
export function atmOf(price: number | null, strikes: readonly number[]): number | null {
  if (price === null || !Number.isFinite(price) || price <= 0 || strikes.length === 0) return null
  let best = strikes[0]
  for (const strike of strikes) {
    const gap = Math.abs(strike - price)
    const bestGap = Math.abs(best - price)
    if (gap < bestGap || (gap === bestGap && strike < best)) best = strike
  }
  return best
}

/**
 * The expiry rule the form can keep a row on for the tab picked, or null when no rule says it. The
 * server's own rule decides (`dynamic.pick_expiries`): the tabs are the open expiries in order, `month`
 * is the first open month-end expiry and `next_month` the one after it, so from a monthly expiry's close
 * to the end of its calendar month "This month's" is already next month's contract (review M2). No
 * clock is read here.
 *
 * The first tab is Nearest. The first monthly tab is This month's, even in second place: Next would
 * leave that date when the nearest expires, This month's keeps it until its own close. Then the second
 * tab is Next and the second monthly tab Next month's. A weekly further out has no rule, and a row
 * cannot follow it.
 */
export function ruleFor(expiries: readonly Expiry[], date: string): ExpiryRule | null {
  const at = expiries.findIndex((each) => each.date === date)
  if (at < 0) return null
  if (at === 0) return 'nearest'
  const monthly = expiries.filter((each) => each.month)
  if (date === monthly[0]?.date) return 'month'
  if (at === 1) return 'next'
  return date === monthly[1]?.date ? 'next_month' : null
}

/** Said under Add as one row on a tab no rule can follow. */
export const NO_RULE =
  'A row can follow the nearest, the next, this month’s or next month’s expiry only. Add CE and Add PE work on this one.'
