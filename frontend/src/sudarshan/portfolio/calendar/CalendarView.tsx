// SUDARSHAN P7 F4 (M7.36): the Calendar view, `/portfolio?view=calendar` (design p7 9.5): what
// waits for the owner's tap first, then Upstox's dividends, bonuses, splits and rights issues for
// what he holds, 90 days back and 180 on. Dates and ratios are public facts read every night, not
// market data: each row says when it was checked and nothing here says "Real-time". The owner's
// page only; a family viewer is offered none of it.
import { useQuery } from '@tanstack/react-query'
import { Info, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  getCalendar,
  listCorporateActions,
  PortfolioError,
  type PortfolioResult,
  problemOf,
} from '../api'
import { dayOfInstant } from '../days'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { dayText, monthOf } from '../tab/format'
import type { CalendarRow, TabInfo } from '../types'
import { checkedText, factText, holdText, KINDS } from './format'
import { ToConfirm } from './ToConfirm'

export const CALENDAR_KEY = [...PORTFOLIO_KEY, 'calendar'] as const
/** The view's own sentence while its switch is off on the server (the ops answer `not_ready`). */
export const CALENDAR_OFF =
  'The calendar of dividends, bonuses and splits is switched off on the server. It opens here once a release switches it on.'
export const NO_FUTURE =
  'Upstox has not listed upcoming events yet; this list shows the ones already announced.'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'

async function need<T>(call: Promise<PortfolioResult<T>>): Promise<T> {
  const result = await call
  if (!result.enabled) {
    throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
  }
  return result.data
}

function Event({ row, today }: { row: CalendarRow; today: number }) {
  const Icon = KINDS[row.kind].icon
  const hold = holdText(row, today)
  return (
    <li className="flex flex-col gap-0.5 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 text-[0.9375rem] font-semibold break-words">
          {row.name ?? row.isin}
        </span>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2 py-0.5 text-xs font-semibold">
          <Icon aria-hidden="true" className="size-3.5" />
          {KINDS[row.kind].word}
        </span>
      </div>
      <span className="text-sm text-[color:var(--text-secondary)]">
        {dayText(row.ex_day)} · {factText(row)}
      </span>
      {hold && <span className="text-sm font-semibold">{hold}</span>}
      <span className="text-xs text-[color:var(--text-tertiary)]">{checkedText(row, today)}</span>
    </li>
  )
}

/** The rows of one stretch of the calendar, month by month in the order given. */
function Months({ title, rows, today }: { title: string; rows: CalendarRow[]; today: number }) {
  if (rows.length === 0) return null
  const months = new Map<number, { label: string; rows: CalendarRow[] }>()
  for (const row of rows) {
    const { key, label } = monthOf(row.ex_day)
    const month = months.get(key) ?? { label, rows: [] }
    month.rows.push(row)
    months.set(key, month)
  }
  return (
    <section aria-label={title} className={cn(CARD, 'flex flex-col gap-1')}>
      <h3 className="text-base font-bold">{title}</h3>
      {[...months.entries()].map(([key, month]) => (
        <div key={key}>
          <h4 className="pt-1.5 text-xs font-semibold text-muted-foreground">{month.label}</h4>
          <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
            {month.rows.map((row) => (
              <Event key={row.event_id} row={row} today={today} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

export function CalendarView({ tabs }: { tabs: readonly TabInfo[] }) {
  const today = dayOfInstant(Date.now())
  const options = { retry: false, refetchOnWindowFocus: false } as const
  const calendar = useQuery({
    queryKey: [...CALENDAR_KEY, 'events'] as const,
    queryFn: ({ signal }) => need(getCalendar({}, signal)),
    ...options,
  })
  const actions = useQuery({
    queryKey: [...CALENDAR_KEY, 'to-confirm'] as const,
    queryFn: ({ signal }) => need(listCorporateActions({}, signal)),
    ...options,
  })
  const refresh = () => {
    void calendar.refetch()
    void actions.refetch()
  }
  const failed = calendar.error ?? actions.error
  const problem = failed ? problemOf(failed) : null
  const rows = calendar.data?.calendar ?? []
  const coming = rows.filter((r) => r.ex_day >= today).sort((a, b) => a.ex_day - b.ex_day)
  const past = rows.filter((r) => r.ex_day < today).sort((a, b) => b.ex_day - a.ex_day)
  return (
    <div className="flex flex-col gap-3">
      <h2 className="sd-display text-[1.375rem] leading-tight font-bold">Calendar</h2>
      {problem !== null ? (
        <div className={cn(CARD, 'flex flex-col items-start gap-2')} aria-live="polite">
          <p className="text-sm text-[color:var(--text-secondary)]">
            {problem.code === 'not_ready' ? CALENDAR_OFF : problem.message}
          </p>
          {problem.code !== 'not_ready' && (
            <button
              type="button"
              onClick={refresh}
              className="inline-flex min-h-12 items-center gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-4 text-[0.9375rem] font-semibold text-[color:var(--brand)]"
            >
              <RefreshCw aria-hidden="true" className="size-4" />
              Try again
            </button>
          )}
        </div>
      ) : calendar.data === undefined || actions.data === undefined ? (
        <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')} aria-live="polite">
          Reading the calendar.
        </p>
      ) : (
        <>
          <ToConfirm events={actions.data.to_confirm} tabs={tabs} onDone={refresh} />
          {!calendar.data.listed_future && (
            <p
              role="note"
              className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--warning-text)]"
            >
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {NO_FUTURE}
            </p>
          )}
          <Months title="Coming up" rows={coming} today={today} />
          <Months title="The last 90 days" rows={past} today={today} />
          {rows.length === 0 && (
            <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')}>
              No dividend, bonus, split or rights issue is listed for what you hold, from{' '}
              {dayText(calendar.data.from)} to {dayText(calendar.data.to)}.
            </p>
          )}
        </>
      )}
    </div>
  )
}
