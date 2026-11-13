/**
 * SUDARSHAN (v0.8.1 review N3): a page left open across the Indian date
 * change. What an instrument's own facts say (an expiring contract's stop, a
 * closing window) belongs to one day; asked once when a page loads, they are
 * yesterday's the next morning. This calls back once when the date in India
 * changes, so the page asks again.
 *
 * It is not market data and never a poll: one timeout runs to the next
 * midnight in India, and a page that was asleep when it fell due (a phone) is
 * caught when it becomes visible or is focused again, by comparing the date
 * the facts belong to with today's.
 */

const IST_OFFSET_MS = 19_800_000
const DAY_MS = 86_400_000
/** The timeout is set a moment past midnight, so the date has certainly turned when it fires. */
const PAST_MIDNIGHT_MS = 250

/** The date in India for an instant, as 2026-12-08. */
export function istDay(ms: number): string {
  return new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 10)
}

/** Milliseconds from an instant to the next midnight in India. */
export function msToNextIstMidnight(ms: number): number {
  const local = ms + IST_OFFSET_MS
  return DAY_MS - (((local % DAY_MS) + DAY_MS) % DAY_MS)
}

/**
 * Call `onNewDay` (with the new date) each time the date in India changes while this is
 * watching. Returns the function that stops it. `day` is the date the page's facts belong to,
 * today unless the caller knows better.
 */
export function watchIstDay(onNewDay: (day: string) => void, day = istDay(Date.now())): () => void {
  let known = day
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  const arm = () => {
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      check()
    }, msToNextIstMidnight(Date.now()) + PAST_MIDNIGHT_MS)
  }
  const check = () => {
    if (stopped) return
    const today = istDay(Date.now())
    if (today !== known) {
      known = today
      arm()
      onNewDay(today)
      return
    }
    // Fired a little early, or a wake-up on the same day: keep the one timeout to midnight.
    if (timer === null) arm()
  }
  const wake = () => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    check()
  }

  arm()
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', wake)
  if (typeof window !== 'undefined') window.addEventListener('focus', wake)
  return () => {
    stopped = true
    if (timer !== null) clearTimeout(timer)
    timer = null
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', wake)
    if (typeof window !== 'undefined') window.removeEventListener('focus', wake)
  }
}
