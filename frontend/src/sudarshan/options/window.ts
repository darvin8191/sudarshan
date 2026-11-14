/**
 * What the options screen follows on the socket (M11.1, O3), free of React like `watchlist/follow.ts`.
 *
 * The screen follows at most `FOLLOW_CAP` instruments on OpenAlgo's pooled connection: the spot, the
 * nearest future, the open sheet's instruments, then the strikes of the window (the at-the-money strike
 * and `LIVE_SIDE` either side, two legs each), counted from the middle outward. A strike that does not
 * fit whole is not followed and nothing after it is. Every strike outside keeps the snapshot's figures
 * and says their time.
 *
 * The window is centred when a chain opens and moved only when the at-the-money strike has walked
 * `RECENTRE_STRIKES` strikes or more from its centre, at most once in `RECENTRE_MIN_MS`. The socket
 * then changes by difference (`useFrameLines`): the strikes that stay are never unsubscribed.
 */

/** The most instruments the screen follows at once (design 2.2). */
export const FOLLOW_CAP = 100

/** Strikes either side of the at-the-money strike that tick. */
export const LIVE_SIDE = 20

/** Strikes either side the Chain tab draws before "Show more strikes". */
export const SHOWN_SIDE = 10

/** The window moves when the at-the-money strike is this many strikes from its centre. */
export const RECENTRE_STRIKES = 2

/** The window moves at most this often. */
export const RECENTRE_MIN_MS = 30_000

/** One strike's two instruments, as EXCHANGE:SYMBOL; a side the chain lacks is absent. */
export interface StrikeKeys {
  strike: number
  ce?: string
  pe?: string
}

export interface FollowPlan {
  /** Followed in Quote mode (Upstox `full`): the legs, the future, a stock's spot, the sheet. */
  quote: readonly string[]
  /** Followed in LTP mode: an index's spot. */
  ltp: readonly string[]
  /** The strikes whose legs are all followed. */
  strikes: ReadonlySet<number>
}

/** The strike nearest the price, a tie going to the lower strike (OpenAlgo's rule). Sorted or not. */
export function atmStrike(price: number | null, strikes: readonly number[]): number | null {
  if (price === null || !Number.isFinite(price) || strikes.length === 0) return null
  let best = strikes[0]
  for (const strike of strikes) {
    const nearer = Math.abs(strike - price) - Math.abs(best - price)
    if (nearer < 0 || (nearer === 0 && strike < best)) best = strike
  }
  return best
}

/** The strikes within `side` places of `centre` in an ascending ladder; the middle of it with no centre. */
export function around(strikes: readonly number[], centre: number | null, side: number): number[] {
  if (strikes.length === 0) return []
  const found = centre === null ? -1 : strikes.indexOf(centre)
  const mid = found >= 0 ? found : Math.floor(strikes.length / 2)
  return strikes.slice(Math.max(0, mid - side), mid + side + 1)
}

/**
 * Where the window is centred now. It stays on `centre` until the at-the-money strike is
 * `RECENTRE_STRIKES` or more places from it and `RECENTRE_MIN_MS` has passed since it last moved;
 * a centre the ladder no longer lists, or none yet, takes the at-the-money strike at once.
 */
export function nextCentre({
  strikes,
  centre,
  atm,
  now,
  movedAt,
}: {
  strikes: readonly number[]
  centre: number | null
  atm: number | null
  now: number
  movedAt: number | null
}): number | null {
  if (atm === null) return centre !== null && strikes.includes(centre) ? centre : null
  const at = centre === null ? -1 : strikes.indexOf(centre)
  if (at < 0) return atm
  const walked = Math.abs(strikes.indexOf(atm) - at)
  if (walked < RECENTRE_STRIKES) return centre
  if (movedAt !== null && now - movedAt < RECENTRE_MIN_MS) return centre
  return atm
}

/**
 * The instruments to follow, never more than `FOLLOW_CAP`: the spot, the future, the sheet's, then
 * the window's strikes from the centre outward (the nearer first, the lower of a pair first).
 */
export function followPlan({
  ladder,
  centre,
  spot,
  spotMode,
  future = null,
  sheet = [],
  side = LIVE_SIDE,
}: {
  /** Every strike of the expiry, ascending. */
  ladder: readonly StrikeKeys[]
  centre: number | null
  spot: string | null
  spotMode: 'LTP' | 'Quote'
  future?: string | null
  sheet?: readonly string[]
  side?: number
}): FollowPlan {
  const quote = new Set<string>()
  const ltp = new Set<string>()
  const count = () => quote.size + ltp.size
  if (spot !== null) (spotMode === 'LTP' ? ltp : quote).add(spot)
  for (const key of [...(future === null ? [] : [future]), ...sheet]) {
    if (count() >= FOLLOW_CAP) break
    if (!ltp.has(key)) quote.add(key)
  }
  const strikes = new Set<number>()
  const window = around(
    ladder.map((each) => each.strike),
    centre,
    side
  )
  const middle = centre !== null && window.includes(centre) ? centre : window[window.length >> 1]
  const order = [...window].sort((a, b) => Math.abs(a - middle) - Math.abs(b - middle) || a - b)
  const byStrike = new Map(ladder.map((each) => [each.strike, each]))
  for (const strike of order) {
    const each = byStrike.get(strike)
    const wanted = [each?.ce, each?.pe].filter((key): key is string => key !== undefined)
    const fresh = wanted.filter((key) => !quote.has(key) && !ltp.has(key)).length
    if (count() + fresh > FOLLOW_CAP) break
    for (const key of wanted) if (!ltp.has(key)) quote.add(key)
    strikes.add(strike)
  }
  return { quote: [...quote], ltp: [...ltp], strikes }
}

/** How many instruments a plan follows: the figure the cap is held on. */
export function followed(plan: FollowPlan): number {
  return plan.quote.length + plan.ltp.length
}
