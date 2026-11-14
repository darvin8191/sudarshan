// SUDARSHAN P7 F8 (M7.34): the Goals view, `/portfolio?view=goals` (design p7 9.4). Each goal: its
// name, a bar with the percentage in figures, the amount of the target, the date with the months
// left, and what to add each month at the owner's assumed return, or that it is on track. A goal
// that counts tabs moves with their ticks, and its meta line shows where the feed stands
// ("Real-time" only while ticks arrive); a typed amount says it does not move. The monthly amount
// is the service's own sum at its last read. The owner's page only.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { PencilLine, Plus } from 'lucide-react'
import { useCallback, useState } from 'react'
import { cn } from '@/lib/utils'
import { formatAmount } from '@/sudarshan/dashboard/format'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { listGoals, PortfolioError, problemOf } from '../api'
import { dateTimeOfInstant } from '../days'
import { pctNumber } from '../Signed'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { monthOf, rupeesText } from '../tab/format'
import { GOALS_MAX, type Goal, type GoalsList, type TabInfo } from '../types'
import { GoalSheet } from './GoalSheet'
import { type GoalNow, goalNow, type TabNow, TabValue } from './live'

export const GOALS_KEY = [...PORTFOLIO_KEY, 'goals'] as const
/** The view's own sentence while its switch is off on the server (the ops answer `not_ready`). */
export const GOALS_OFF =
  'Goals are switched off on the server. They open here once a release switches them on.'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-4 text-[0.9375rem] font-semibold text-[color:var(--brand)] outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** "By March 2031 · 52 months left". */
export function byWords(goal: Goal): string {
  const left = goal.months_left === 1 ? '1 month left' : `${goal.months_left} months left`
  return `By ${monthOf(goal.target_day).label} · ${left}`
}

/** What to add each month at the owner's assumed return, or that nothing more is needed. */
export function addWords(goal: Goal): string {
  if (goal.on_track) return 'On track without adding more'
  return `Add about ${rupeesText(goal.required_monthly_paise)} a month at ${goal.rate_bp / 100}% a year (your assumption)`
}

function GoalCard({ goal, now, onEdit }: { goal: Goal; now: GoalNow; onEdit: () => void }) {
  const progress = pctNumber(now.progress_pct) ?? 0
  return (
    <article aria-label={goal.name} className={cn(CARD, 'flex flex-col gap-2')}>
      <div className="flex items-start justify-between gap-3">
        <span className="flex min-w-0 flex-col">
          <span className="text-base font-bold break-words">{goal.name}</span>
          {goal.name_alt && (
            <span className="text-sm text-[color:var(--text-secondary)]">{goal.name_alt}</span>
          )}
        </span>
        <button
          type="button"
          className={BUTTON}
          aria-label={`Change ${goal.name}`}
          onClick={onEdit}
        >
          <PencilLine aria-hidden="true" className="size-5" />
        </button>
      </div>
      <span className="sd-display text-[1.75rem] leading-tight font-bold">
        {formatAmount(progress, 1)}%
      </span>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.max(0, Math.round(progress)))}
        aria-label={`${goal.name}: progress`}
        className="h-2.5 rounded-full bg-[color:var(--divider)]"
      >
        <div
          className="h-2.5 rounded-full bg-[color:var(--brand)]"
          style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
        />
      </div>
      <span className="text-[0.9375rem] font-semibold">
        <span className="whitespace-nowrap">{rupeesText(now.value_paise)}</span> of{' '}
        <span className="whitespace-nowrap">{rupeesText(goal.target_paise)}</span>
      </span>
      <span className="text-sm text-[color:var(--text-secondary)]">{byWords(goal)}</span>
      <span className="text-sm">{addWords(goal)}</span>
      {now.line !== null ? (
        <LiveLine line={now.line} now={now.now} />
      ) : (
        <span className="text-xs text-[color:var(--text-tertiary)]">
          {goal.fixed_paise !== null
            ? 'Counts the amount you typed; it does not move with prices.'
            : 'As the service last read it.'}
        </span>
      )}
    </article>
  )
}

export function GoalsView({ tabs }: { tabs: readonly TabInfo[] }) {
  const queries = useQueryClient()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const [editing, setEditing] = useState<Goal | 'new' | null>(null)
  const [values, setValues] = useState<ReadonlyMap<number, TabNow>>(new Map())
  const onValue = useCallback((tabId: number, now: TabNow) => {
    setValues((was) => {
      const kept = was.get(tabId)
      if (
        kept &&
        kept.value_paise === now.value_paise &&
        kept.line === now.line &&
        kept.now === now.now
      ) {
        return was
      }
      return new Map(was).set(tabId, now)
    })
  }, [])
  const key = [...GOALS_KEY, 'list'] as const
  const query = useQuery({
    queryKey: key,
    queryFn: async ({ signal }): Promise<GoalsList> => {
      const result = await listGoals(signal)
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  })
  const problem = query.error ? problemOf(query.error) : null
  const goals = query.data?.goals ?? []
  const active = goals.filter((goal) => !goal.archived)
  const archived = goals.filter((goal) => goal.archived)
  const known = new Set(tabs.map((tab) => tab.id))
  const linked = [...new Set(active.flatMap((goal) => goal.tab_ids))].filter((id) => known.has(id))
  return (
    <div className="flex flex-col gap-3">
      <MarketCalendarSource key={day} onChange={setMarket} />
      {linked.map((id) => (
        <TabValue key={id} tabId={id} marketOpen={isOpen} onValue={onValue} />
      ))}
      <div className="flex items-center justify-between gap-3">
        <h2 className="sd-display text-[1.375rem] leading-tight font-bold">Goals</h2>
        {query.data !== undefined && active.length < GOALS_MAX && (
          <button type="button" className={BUTTON} onClick={() => setEditing('new')}>
            <Plus aria-hidden="true" className="size-4" />
            Goal
          </button>
        )}
      </div>
      {problem !== null ? (
        <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')} aria-live="polite">
          {problem.code === 'not_ready' ? GOALS_OFF : problem.message}
        </p>
      ) : query.data === undefined ? (
        <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')} aria-live="polite">
          Reading your goals.
        </p>
      ) : (
        <>
          {active.length === 0 && (
            <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')}>
              No goal yet. Add one with a target, a date and the tabs that count towards it.
            </p>
          )}
          {active.map((goal) => (
            <GoalCard
              key={goal.id}
              goal={goal}
              now={goalNow(goal, values)}
              onEdit={() => setEditing(goal)}
            />
          ))}
          {archived.length > 0 && (
            <details className={CARD}>
              <summary className="flex min-h-12 cursor-pointer items-center text-[0.9375rem] font-semibold">
                Archived goals ({archived.length})
              </summary>
              <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
                {archived.map((goal) => (
                  <li key={goal.id}>
                    <button
                      type="button"
                      className="flex min-h-12 w-full items-center justify-between gap-3 text-left text-[0.9375rem]"
                      onClick={() => setEditing(goal)}
                    >
                      <span className="min-w-0 break-words">{goal.name}</span>
                      <span className="shrink-0 whitespace-nowrap">
                        {rupeesText(goal.target_paise)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="text-xs text-[color:var(--text-tertiary)]">
            The monthly amounts are as of {dateTimeOfInstant(query.data.as_of).slice(0, -3)}.
          </p>
        </>
      )}
      <GoalSheet
        open={editing !== null}
        goal={editing === 'new' ? null : editing}
        tabs={tabs}
        onClose={() => setEditing(null)}
        onSaved={(list) => {
          queries.setQueryData(key, list)
          setEditing(null)
        }}
      />
    </div>
  )
}
