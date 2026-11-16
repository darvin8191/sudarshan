// SUDARSHAN P7 F8 (M7.34): a goal's value moving with ticks. A goal counts the value of its tabs,
// so each linked tab is read once (`tab_get`, the query the tab's own screen uses) and priced by
// the same `useTabLive` as its Overview: ticks through the feed, one draw a frame, no polling.
// Until every linked tab has reported, the goal shows the service's own figure.
//
// What a tab reports upward is compared by what it says, never by which object says it:
// `useTabLive` builds its feed line anew on every render, and reporting that object made the
// Goals view and this component redraw each other without end (review B1).
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'
import type { FeedLine, MarketOpen } from '@/sudarshan/dashboard/live'
import { pct } from '../money'
import { tabViewOptions } from '../tab/tabView'
import type { Goal } from '../types'
import { useTabLive } from '../useTabLive'

export interface TabNow {
  value_paise: number
  line: FeedLine
}

/** Everything a feed line says, as text: two lines with the same text say the same thing. */
export function lineKey(line: FeedLine): string {
  const at = 'at' in line ? line.at : null
  const stale = 'stale' in line ? line.stale : false
  const again = line.kind === 'connecting' ? line.again : false
  const stopped = line.kind === 'closed' ? (line.stoppedAt ?? null) : null
  return JSON.stringify([line.kind, at, stale, again, stopped])
}

/** Whether two reports of a tab say the same: its value, and where its feed stands. */
export function sameTabNow(a: TabNow, b: TabNow): boolean {
  return a.value_paise === b.value_paise && lineKey(a.line) === lineKey(b.line)
}

const NOTHING = {
  realised_paise: 0,
  income_paise: 0,
  fees_paise: 0,
  money_in_paise: 0,
  money_out_paise: 0,
  closed_today_paise: 0,
  closed_today_base_paise: 0,
  liabilities_paise: 0,
}
const NO_ROWS: never[] = []

/**
 * Draws nothing: prices one tab from ticks and says what it is worth now. It reports only when
 * the value or what the feed line says has changed, however often it renders (the clock of
 * `useTabLive` renders it once a second).
 */
export function TabValue({
  tabId,
  marketOpen,
  onValue,
}: {
  tabId: number
  marketOpen: MarketOpen
  onValue: (tabId: number, now: TabNow) => void
}) {
  const { data } = useQuery(tabViewOptions(tabId, {}))
  const live = useTabLive({
    rows: data?.rows ?? NO_ROWS,
    fixed: data?.fixed ?? NOTHING,
    readAt: data?.read_at ?? Number.MAX_SAFE_INTEGER,
    marketOpen,
    enabled: data !== undefined,
  })
  const value = data === undefined ? null : live.totals.value_paise
  const key = lineKey(live.line)
  // The same words give the same object, so the effect below runs only when something changed.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` is everything `live.line` says
  const line = useMemo(() => live.line, [key])
  useEffect(() => {
    if (value !== null) onValue(tabId, { value_paise: value, line })
  }, [tabId, value, line, onValue])
  return null
}

/**
 * Where a goal's figure comes from: `ticks` (its tabs, priced now), `still` (its tabs, none of
 * which holds anything with a live price), `typed` (an amount typed by hand) or `read` (the
 * service's last read, until every tab has reported).
 */
export type GoalBasis = 'ticks' | 'still' | 'typed' | 'read'

export interface GoalNow {
  value_paise: number
  /** The percentage text of four decimals, as the service writes it; not capped. */
  progress_pct: string
  /** Where the feed of its tabs stands; null unless the basis is `ticks`. */
  line: FeedLine | null
  basis: GoalBasis
}

/**
 * A goal's value and progress now: from its tabs' live values once all are in, else as read. A
 * tab that was deleted counts as nothing, as on the service.
 *
 * The feed line is of the tabs that can tick: a tab of gold or deposits only has no price coming
 * (`idle`) and does not silence the others. "Real-time" when every tab that can tick is ticking,
 * else the first that is not (review M1).
 */
export function goalNow(goal: Goal, tabs: ReadonlyMap<number, TabNow>): GoalNow {
  const read = {
    value_paise: goal.value_now_paise,
    progress_pct: goal.progress_pct,
    line: null,
    basis: 'read' as GoalBasis,
  }
  if (goal.problem !== null) return read
  if (goal.fixed_paise !== null) return { ...read, basis: 'typed' }
  const gone = new Set(goal.gone_tab_ids)
  const ids = goal.tab_ids.filter((id) => !gone.has(id))
  if (ids.length === 0) return read
  const parts = ids.map((id) => tabs.get(id))
  if (parts.some((part) => part === undefined)) return read
  const known = parts as TabNow[]
  const value = known.reduce((sum, part) => sum + part.value_paise, 0)
  const ticking = known.map((part) => part.line).filter((line) => line.kind !== 'idle')
  const figures = {
    value_paise: value,
    progress_pct: pct(value, goal.target_paise) ?? goal.progress_pct,
  }
  if (ticking.length === 0) return { ...figures, line: null, basis: 'still' }
  const line = ticking.find((each) => each.kind !== 'live') ?? ticking[0]
  return { ...figures, line, basis: 'ticks' }
}
