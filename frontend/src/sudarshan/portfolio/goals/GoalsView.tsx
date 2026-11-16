// SUDARSHAN P7 F8 (M7.34): the Goals view, `/portfolio?view=goals` (design p7 9.4). Each goal: its
// name, a bar with the percentage in figures, the amount of the target, the date with the months
// left, and what to add each month at the owner's assumed return, or that it is on track. A goal
// that counts tabs moves with their ticks, and its meta line shows where the feed stands
// ("Real-time" only while ticks arrive); a typed amount says it does not move. The amount to add
// is the service's own sum at its last read, and is worked out again on the phone when the value
// moves (sums.ts). A goal past its date, a tab that was deleted or archived and a goal whose
// figures could not be worked out each say so in words. The owner's page only.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { PencilLine, Plus, TriangleAlert } from 'lucide-react'
import { useCallback, useState } from 'react'
import { cn } from '@/lib/utils'
import { formatAmount } from '@/sudarshan/dashboard/format'
import { useMarketOpen, useNow } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { FeedLine } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { listGoals, PortfolioError, problemOf } from '../api'
import { dateTimeOfInstant } from '../days'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { rupeesText } from '../tab/format'
import { GOALS_MAX, type Goal, type GoalsList, type TabInfo } from '../types'
import { GoalSheet } from './GoalSheet'
import { type GoalNow, goalNow, sameTabNow, type TabNow, TabValue } from './live'
import { addWords, archivedWords, byWords, goneWords, shownProgress } from './sums'

export const GOALS_KEY = [...PORTFOLIO_KEY, 'goals'] as const
/** The view's own sentence while its switch is off on the server (the ops answer `not_ready`). */
export const GOALS_OFF =
  'Goals are switched off on the server. They open here once a release switches them on.'

const NO_TABS: readonly TabInfo[] = []
const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-4 text-[0.9375rem] font-semibold text-[color:var(--brand)] outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

const BASIS_WORDS = {
  typed: 'Counts the amount you typed; it does not move with prices.',
  still: 'Counts holdings that have no live price; it does not move during the day.',
  read: 'As the service last read it.',
} as const

/** The feed line of a goal's tabs. The age it shows moves every second, so the clock is its own. */
function GoalLine({ line }: { line: FeedLine }) {
  const now = useNow()
  return <LiveLine line={line} now={now} />
}

function Warning({ children }: { children: string }) {
  return (
    <span className="flex items-start gap-1.5 text-sm font-semibold text-[color:var(--warning-text)]">
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0">{children}</span>
    </span>
  )
}

function GoalCard({
  goal,
  now,
  today,
  archivedTabs,
  onEdit,
}: {
  goal: Goal
  now: GoalNow
  today: number
  /** The names of the archived tabs this goal counts. */
  archivedTabs: readonly string[]
  onEdit: () => void
}) {
  const progress = shownProgress(now.progress_pct)
  const gone = goneWords(goal.gone_tab_ids.length)
  const shelved = archivedWords(archivedTabs)
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
      {goal.problem !== null ? (
        <Warning>{goal.problem}</Warning>
      ) : (
        <>
          <span className="sd-display text-[1.75rem] leading-tight font-bold">
            {formatAmount(progress, 1)}%
          </span>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.min(100, Math.floor(progress))}
            aria-label={`${goal.name}: progress`}
            className="h-2.5 rounded-full bg-[color:var(--divider)]"
          >
            <div
              className="h-2.5 rounded-full bg-[color:var(--brand)]"
              style={{ width: `${Math.min(100, progress)}%` }}
            />
          </div>
          <span className="text-[0.9375rem] font-semibold">
            <span className="whitespace-nowrap">{rupeesText(now.value_paise)}</span> of{' '}
            <span className="whitespace-nowrap">{rupeesText(goal.target_paise)}</span>
          </span>
          <span className="text-sm text-[color:var(--text-secondary)]">{byWords(goal, today)}</span>
          <span className="text-sm">{addWords(goal, now.value_paise, today)}</span>
          {gone !== null && <Warning>{gone}</Warning>}
          {shelved !== null && (
            <span className="text-sm text-[color:var(--text-secondary)]">{shelved}</span>
          )}
          {now.line !== null ? (
            <GoalLine line={now.line} />
          ) : (
            <span className="text-xs text-[color:var(--text-tertiary)]">
              {BASIS_WORDS[now.basis === 'ticks' ? 'read' : now.basis]}
            </span>
          )}
        </>
      )}
    </article>
  )
}

export function GoalsView({
  tabs,
  archivedTabs = NO_TABS,
}: {
  /** The owner's own tabs that a goal may count. */
  tabs: readonly TabInfo[]
  /** His archived tabs: a goal that counts one still does, and says so. */
  archivedTabs?: readonly TabInfo[]
}) {
  const queries = useQueryClient()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const today = Number(day.replaceAll('-', ''))
  const [editing, setEditing] = useState<Goal | 'new' | null>(null)
  const [values, setValues] = useState<ReadonlyMap<number, TabNow>>(new Map())
  const onValue = useCallback((tabId: number, now: TabNow) => {
    setValues((was) => {
      // Compared by what it says: the same report, in a new object, changes nothing (review B1).
      const kept = was.get(tabId)
      return kept && sameTabNow(kept, now) ? was : new Map(was).set(tabId, now)
    })
  }, [])
  // The day is part of the key: the months left and the amount are read again when it turns.
  const key = [...GOALS_KEY, 'list', day] as const
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
  const shelved = new Map(archivedTabs.map((tab) => [tab.id, tab.name]))
  const known = new Set([...tabs.map((tab) => tab.id), ...shelved.keys()])
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
              today={today}
              archivedTabs={goal.tab_ids.flatMap((id) => shelved.get(id) ?? [])}
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
            Read at {dateTimeOfInstant(query.data.as_of).slice(0, -3)}. The amount to add follows
            the value shown. A goal counts a tab's value before its loans.
          </p>
        </>
      )}
      <GoalSheet
        open={editing !== null}
        goal={editing === 'new' ? null : editing}
        tabs={tabs}
        archivedTabs={archivedTabs}
        onStale={() => void queries.invalidateQueries({ queryKey: key })}
        onClose={() => setEditing(null)}
        onSaved={(list) => {
          queries.setQueryData(key, list)
          setEditing(null)
        }}
      />
    </div>
  )
}
