// SUDARSHAN P7 F4 (M7.36): the Calendar view, `/portfolio?view=calendar` (design p7 9.5): what
// waits for the owner's tap first, then Upstox's dividends, bonuses, splits and rights issues for
// what he holds, 90 days back and 180 on. Dates and ratios are public facts read every night, not
// market data: each row says when it was checked and nothing here says "Real-time". Every bonus
// and split he confirmed is listed under Confirmed, whatever its day, and can be changed there.
// The owner's page only; a family viewer is offered none of it.
// GA8.2: the shares on his watchlists too, each such row marked "Watchlist" with its icon, and a
// choice of Holdings, Watchlists or All; the lists say when they were read ("as of").
import { useQuery } from '@tanstack/react-query'
import { Eye, Info, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Segmented } from '@/sudarshan/journal/Segmented'
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
import type { CaConfirmed, CalendarRow, TabInfo, WatchlistsRead } from '../types'
import { Decided } from './Decided'
import {
  type CalendarShow,
  checkedText,
  emptyText,
  factText,
  holdText,
  KINDS,
  SHOWS,
  shownIn,
  watchlistsAsOf,
} from './format'
import { LotCheckCard } from './LotCheckCard'
import { ToConfirm } from './ToConfirm'

export const CALENDAR_KEY = [...PORTFOLIO_KEY, 'calendar'] as const
/** The view's own sentence while its switch is off on the server (the ops answer `not_ready`). */
export const CALENDAR_OFF =
  'The calendar of dividends, bonuses and splits is switched off on the server. It opens here once a release switches it on.'
export const NO_FUTURE =
  'Upstox has not listed upcoming events yet; this list shows the ones already announced.'

/**
 * Bonuses and splits not asked about yet: no closing record reaches their day, and until the
 * year's share trades are read in full nothing can tell whether the share was held then.
 */
export function leftOutWords(count: number): string {
  return count === 1
    ? '1 earlier bonus or split is not asked about yet: SUDARSHAN cannot tell what you held on its date until Upstox’s share trades are read in full.'
    : `${count} earlier bonuses or splits are not asked about yet: SUDARSHAN cannot tell what you held on their dates until Upstox’s share trades are read in full.`
}

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
/**
 * A row's name and its chips (v0.17.0 phone review L7): the name never narrows below its longest
 * word (the flex item's own minimum), so it breaks between words ("Infrastructur" over "e
 * Holdings" at 320 px with Larger text); where that word and the chips do not fit side by side,
 * the chips move under it, at the right. Only a word wider than the whole row is broken.
 */
const NAME_ROW = 'flex flex-wrap items-start justify-between gap-x-3 gap-y-1'
const NAME = 'max-w-full flex-[1_1_0%] text-[0.9375rem] font-semibold break-words'

async function need<T>(call: Promise<PortfolioResult<T>>): Promise<T> {
  const result = await call
  if (!result.enabled) {
    throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
  }
  return result.data
}

/**
 * Every bonus and split confirmed for the Upstox account, whatever its day and whether or not
 * Upstox still lists it (v0.14.0 verification N1), each with "Change what I confirmed".
 */
function Confirmed({ rows, onDone }: { rows: readonly CaConfirmed[]; onDone: () => void }) {
  if (rows.length === 0) return null
  return (
    <section aria-label="Confirmed" className={cn(CARD, 'flex flex-col gap-1')}>
      <h3 className="text-base font-bold">Confirmed</h3>
      <p className="text-xs text-[color:var(--text-tertiary)]">
        Each bonus and split you confirmed for the Upstox account. A change reaches the tax lots at
        once.
      </p>
      <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
        {rows.map((row) => {
          const Icon = KINDS[row.kind].icon
          return (
            <li key={row.event_id} className="flex flex-col gap-0.5 py-2.5">
              <div className={NAME_ROW}>
                <span className={NAME}>{row.name ?? row.isin}</span>
                <span className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2 py-0.5 text-xs font-semibold">
                  <Icon aria-hidden="true" className="size-3.5" />
                  {KINDS[row.kind].word}
                </span>
              </div>
              <span className="text-sm text-[color:var(--text-secondary)]">
                Ex-date {dayText(row.ex_day)} · {factText({ ...row, amount_u: null })}
              </span>
              {row.gone_at !== null && (
                <span className="text-xs text-[color:var(--text-tertiary)]">
                  Upstox no longer lists it; what you confirmed still counts.
                </span>
              )}
              <Decided kind={row.kind} decided={row.decided} onDone={onDone} />
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Event({ row, today }: { row: CalendarRow; today: number }) {
  const Icon = KINDS[row.kind].icon
  const hold = holdText(row, today)
  return (
    <li className="flex flex-col gap-0.5 py-2.5">
      <div className={NAME_ROW}>
        <span className={NAME}>{row.name ?? row.isin}</span>
        <span className="ml-auto flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-start">
          <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2 py-0.5 text-xs font-semibold">
            <Icon aria-hidden="true" className="size-3.5" />
            {KINDS[row.kind].word}
          </span>
          {row.watchlist && (
            // On a watchlist: its icon and its word, never the tint alone.
            <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--brand-tint)] px-2 py-0.5 text-xs font-semibold text-[color:var(--brand)]">
              <Eye aria-hidden="true" className="size-3.5" />
              Watchlist
            </span>
          )}
        </span>
      </div>
      <span className="text-sm text-[color:var(--text-secondary)]">
        {dayText(row.ex_day)} · {factText(row)}
      </span>
      {hold && <span className="text-sm font-semibold">{hold}</span>}
      <span className="text-xs text-[color:var(--text-tertiary)]">{checkedText(row, today)}</span>
      {row.decided && (
        // What stands; it is changed under Confirmed, the one place for every one of them.
        <span className="text-sm">{row.decided.words}</span>
      )}
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

/**
 * When the watchlists' shares were read, and why they are not shown when they are not (GA8.2):
 * not read yet, Watchlists v2 switched off, or a night that could not read them.
 */
function Lists({ read, today }: { read: WatchlistsRead; today: number }) {
  const asOf = watchlistsAsOf(read, today)
  return (
    <>
      {asOf !== null && <p className="text-xs text-[color:var(--text-tertiary)]">{asOf}</p>}
      {read.words !== null && (
        <p
          role="note"
          className={cn(
            'flex items-start gap-2 rounded-[0.875rem] border px-3 py-2 text-sm font-semibold',
            read.state === 'failed'
              ? 'border-[color:var(--gold)] bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]'
              : 'border-[color:var(--border-strong)] bg-card text-[color:var(--text-secondary)]'
          )}
        >
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {read.words}
        </p>
      )}
    </>
  )
}

function Empty({ text }: { text: string | null }) {
  if (text === null) return null
  return <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')}>{text}</p>
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
  const [show, setShow] = useState<CalendarShow>('all')
  const failed = calendar.error ?? actions.error
  const problem = failed ? problemOf(failed) : null
  const rows = (calendar.data?.calendar ?? []).filter((r) => shownIn(r, show))
  const lists = calendar.data?.watchlists
  const status = calendar.data?.read_status
  /** Upstox's list was read: only then does an empty list mean "nothing listed". */
  const read = status === undefined || status.state === 'ok'
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
          <LotCheckCard
            check={actions.data.lot_check}
            onDone={refresh}
            confirmed={actions.data.confirmed ?? []}
          />
          <ToConfirm events={actions.data.to_confirm} tabs={tabs} onDone={refresh} />
          <Confirmed rows={actions.data.confirmed ?? []} onDone={refresh} />
          {actions.data.left_out_count > 0 && (
            <p className="text-sm text-[color:var(--text-secondary)]">
              {leftOutWords(actions.data.left_out_count)}
            </p>
          )}
          {status !== undefined && status.state !== 'ok' && status.words !== null && (
            // Before the first nightly read, or after a failed one: say so, never "nothing
            // listed" (an empty list means that only once the list was read).
            <p
              role="note"
              className={cn(
                'flex items-start gap-2 rounded-[0.875rem] border px-3 py-2 text-sm font-semibold',
                status.state === 'failed'
                  ? 'border-[color:var(--gold)] bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]'
                  : 'border-[color:var(--border-strong)] bg-card text-[color:var(--text-secondary)]'
              )}
            >
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {status.words}
            </p>
          )}
          {read && !calendar.data.listed_future && (
            <p
              role="note"
              className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--warning-text)]"
            >
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {NO_FUTURE}
            </p>
          )}
          <Segmented label="Show the events of" value={show} options={SHOWS} onChange={setShow} />
          {show !== 'holdings' && lists !== undefined && <Lists read={lists} today={today} />}
          <Months title="Coming up" rows={coming} today={today} />
          <Months title="The last 90 days" rows={past} today={today} />
          {read && rows.length === 0 && (
            <Empty
              text={emptyText(
                show,
                lists,
                `from ${dayText(calendar.data.from)} to ${dayText(calendar.data.to)}`
              )}
            />
          )}
        </>
      )}
    </div>
  )
}
