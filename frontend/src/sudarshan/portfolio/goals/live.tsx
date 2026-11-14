// SUDARSHAN P7 F8 (M7.34): a goal's value moving with ticks. A goal counts the value of its tabs,
// so each linked tab is read once (`tab_get`, the query the tab's own screen uses) and priced by
// the same `useTabLive` as its Overview: ticks through the feed, one draw a frame, no polling.
// Until every linked tab has reported, the goal shows the service's own figure.
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import type { FeedLine, MarketOpen } from '@/sudarshan/dashboard/live'
import { pct } from '../money'
import { tabViewOptions } from '../tab/tabView'
import type { Goal } from '../types'
import { useTabLive } from '../useTabLive'

export interface TabNow {
  value_paise: number
  line: FeedLine
  now: number
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

/** Draws nothing: prices one tab from ticks and says what it is worth now. */
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
    rows: data?.rows ?? [],
    fixed: data?.fixed ?? NOTHING,
    readAt: data?.read_at ?? Number.MAX_SAFE_INTEGER,
    marketOpen,
    enabled: data !== undefined,
  })
  const value = data === undefined ? null : live.totals.value_paise
  const { line, now } = live
  useEffect(() => {
    if (value !== null) onValue(tabId, { value_paise: value, line, now })
  }, [tabId, value, line, now, onValue])
  return null
}

export interface GoalNow {
  value_paise: number
  /** The percentage text of four decimals, as the service writes it; not capped. */
  progress_pct: string
  /** Where the feed of its tabs stands; null for a typed amount or before the tabs are read. */
  line: FeedLine | null
  now: number
}

/** A goal's value and progress now: from its tabs' live values once all are in, else as read. */
export function goalNow(goal: Goal, tabs: ReadonlyMap<number, TabNow>): GoalNow {
  const snapshot = {
    value_paise: goal.value_now_paise,
    progress_pct: goal.progress_pct,
    line: null,
    now: 0,
  }
  if (goal.fixed_paise !== null || goal.tab_ids.length === 0) return snapshot
  const parts = goal.tab_ids.map((id) => tabs.get(id))
  if (parts.some((part) => part === undefined)) return snapshot
  const known = parts as TabNow[]
  const value = known.reduce((sum, part) => sum + part.value_paise, 0)
  // Real-time only when every linked tab's ticks are arriving; else the first that is not.
  const line = known.find((part) => part.line.kind !== 'live')?.line ?? known[0].line
  return {
    value_paise: value,
    progress_pct: pct(value, goal.target_paise) ?? goal.progress_pct,
    line,
    now: Math.max(...known.map((part) => part.now)),
  }
}
