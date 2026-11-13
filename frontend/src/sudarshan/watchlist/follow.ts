/**
 * What the watchlist page follows on the socket (F9, M8.8), free of React like `rows.ts`.
 *
 * The page follows at most `FOLLOW_CAP` instruments (rule R4: OpenAlgo's pooled connection). A list
 * longer than that (only a built-in list can be) follows a window of `WINDOW` rows that moves with
 * the scrolling. Everything else keeps the figure it last had and says so.
 */

/** The most instruments the page follows at once. */
export const FOLLOW_CAP = 250

/** The rows of a list longer than the cap that tick: about 55 either side of the ten on screen. */
export const WINDOW = 120

/** The window is recomputed this long after the scrolling stops. */
export const SETTLE_MS = 300

/** One row of the list, in display order. */
export interface FollowRow {
  /** EXCHANGE:SYMBOL of the row. */
  key: string
  /** What the row follows, when that is not just itself: a strike row follows its CE and PE (F12). */
  keys?: readonly string[]
}

export interface FollowPlan {
  /** Every instrument to follow, as EXCHANGE:SYMBOL. Never more than `FOLLOW_CAP`. */
  keys: ReadonlySet<string>
  /** The rows of a long list that are in the window, as a start and an end (exclusive); null when the list is not long. */
  window: { start: number; end: number } | null
}

/**
 * The instruments to follow, counted in this order, stopping at `FOLLOW_CAP`: the open sheet's
 * instruments; the underlying of each follow row on screen (once each; F11); then the rows in
 * display order, a strike row counting 2 (F12). A row that does not fit whole is not followed, and
 * nothing after it is. A list longer than the cap offers only the rows of its window, from `start`.
 */
export function followPlan({
  sheet = [],
  underlyings = [],
  rows,
  start = 0,
}: {
  sheet?: readonly string[]
  underlyings?: readonly string[]
  rows: readonly FollowRow[]
  start?: number
}): FollowPlan {
  const keys = new Set<string>()
  for (const key of [...sheet, ...underlyings]) {
    if (keys.size >= FOLLOW_CAP) break
    keys.add(key)
  }
  const long = rows.length > FOLLOW_CAP
  const from = long ? Math.max(0, Math.min(start, rows.length - WINDOW)) : 0
  const offered = long ? rows.slice(from, from + WINDOW) : rows
  for (const row of offered) {
    const wanted = new Set(row.keys ?? [row.key])
    const fresh = [...wanted].filter((key) => !keys.has(key)).length
    if (keys.size + fresh > FOLLOW_CAP) break
    for (const key of wanted) keys.add(key)
  }
  return { keys, window: long ? { start: from, end: from + offered.length } : null }
}

/** Whether a row is followed: every instrument it follows is in the plan. */
export function rowFollowed(row: FollowRow, plan: FollowPlan): boolean {
  return (row.keys ?? [row.key]).every((key) => plan.keys.has(key))
}

/** The rows on screen, by their place in the list. */
export interface Visible {
  first: number
  last: number
}

/**
 * Where the window starts. It is centred on the first visible row, and stays where it is while the
 * visible rows are inside its middle half, so a move swaps about 30 keys and slow scrolling swaps none.
 * A list no longer than the cap has no window.
 */
export function windowStart(count: number, visible: Visible, current: number | null): number {
  if (count <= FOLLOW_CAP) return 0
  const ideal = Math.max(0, Math.min(visible.first - WINDOW / 2, count - WINDOW))
  if (current === null) return ideal
  const inside = visible.first >= current + WINDOW / 4 && visible.last < current + (WINDOW * 3) / 4
  return inside ? current : ideal
}

/** The key as the market-data cache writes it, back to the instrument. */
export function instrumentOf(key: string): { symbol: string; exchange: string } {
  const at = key.indexOf(':')
  return { exchange: key.slice(0, at), symbol: key.slice(at + 1) }
}

/** The words a row carries instead of a live figure. The cap's are the design's own. */
export function outsideNote(reason: 'cap' | 'window'): string {
  return reason === 'cap'
    ? `not real-time: this list follows ${FOLLOW_CAP} prices at most`
    : `not real-time: this list follows ${WINDOW} rows at a time`
}

/** What a screen reader hears of a row that is not followed. */
export function outsideSpeech(reason: 'cap' | 'window'): string {
  return `outside the ${reason === 'cap' ? FOLLOW_CAP : WINDOW} followed`
}
