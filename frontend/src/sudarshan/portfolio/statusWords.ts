/**
 * Every status has a word (M7.24, design 8.4): colour and icon only ever sit beside one.
 *
 * Today's four states, a price's two freshness states and the feed line's kinds each map to the
 * words a trader reads. A test walks the lists in types.ts and the kinds of `FeedLine`, so a state
 * added later without a word fails the build.
 */
import { feedWords } from '@/sudarshan/dashboard/LiveLine'
import type { FeedLine } from '@/sudarshan/dashboard/live'
import type { FreshnessState, TodayState } from './types'

/** What stands where Today's figure would be, when there is none. */
export const TODAY_WORDS: Record<TodayState, string> = {
  ok: 'Today',
  unknown: 'Today: not known yet',
  pending: 'Today: waiting for the NAV',
  none: 'No Today for this holding',
}

/** The line under a fund's Today while its NAV is not out. */
export const NAV_PENDING_NOTE = 'NAV changes count when AMFI publishes them, around 9 PM.'

/** A price's freshness: "Old" always has a clock beside it on screen, and the word. */
export const FRESHNESS_WORDS: Record<FreshnessState, string> = {
  fresh: 'Up to date',
  old: 'Old',
}

/** "Real-time" only while ticks arrive (never "Live": that is the trading-mode chip). */
export function feedStatusWords(line: FeedLine, now: number): string {
  if (line.kind === 'live') return 'Real-time'
  if (line.kind === 'idle') return 'Nothing to price'
  return feedWords(line, now)
}

/** What a row says about its price when a tick did not move it (beyond the cap, or none yet). */
export function notLiveWords(takenAt: string | null): string {
  return takenAt ? `Price of ${takenAt}` : 'Price from the last read'
}
