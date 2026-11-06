// The three performance widgets of a tab (design 4.6, widgets 6 to 8) with their one shared read
// of the `performance` op. Each widget is the same whether the Overview draws it by the tab's
// layout or the Performance part draws all three; a read that fails says so in words and offers
// another try, without taking the rest of the page with it.
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { useBookSync } from '@/sudarshan/books/useBookSync'
import { problemOf } from '../api'
import { SECONDARY } from '../sheets/TabFields'
import type { Day } from '../types'
import { IncomeCard } from './IncomeCard'
import { Performance } from './Performance'
import { performanceOptions } from './performance'
import { ValueChart } from './ValueChart'

export type PerformanceWidget = 'history' | 'performance' | 'income'

export function PerformanceBlock({
  tabId,
  widget,
  today,
  liveValuePaise,
  unpriced = 0,
}: {
  tabId: number
  widget: PerformanceWidget
  today: Day
  /** The tab's value at the latest tick, or null when it is not live or is narrowed. */
  liveValuePaise: number | null
  /** Holdings with no price yet, for the Return widget to say they are counted at cost. */
  unpriced?: number
}) {
  const query = useQuery(performanceOptions(tabId))
  const { refetch } = query
  useBookSync(() => refetch(), { events: [], resyncMs: null })
  if (query.isPending) {
    return (
      <p
        className="flex items-center justify-center gap-2 py-6 text-sm text-[color:var(--text-secondary)]"
        aria-live="polite"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading the figures
      </p>
    )
  }
  if (query.isError) {
    return (
      <div
        className="flex flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-6 text-center shadow-[var(--shadow-card)]"
        aria-live="polite"
      >
        <TriangleAlert aria-hidden="true" className="size-6 text-[color:var(--text-secondary)]" />
        <p className="text-base font-bold">{problemOf(query.error).message}</p>
        <button type="button" className={SECONDARY} onClick={() => refetch()}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  }
  const report = query.data
  if (widget === 'history') {
    return <ValueChart report={report} today={today} liveValuePaise={liveValuePaise} />
  }
  if (widget === 'income') return <IncomeCard report={report} />
  return <Performance report={report} unpriced={unpriced} />
}
