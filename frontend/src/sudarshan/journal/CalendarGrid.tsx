// SUDARSHAN M14.3, JD4: the calendar. A month grid in which every day with trades carries its net
// as text with its sign, a small arrow and a tint by sign (the tint is the third sign, never the
// only one), plus the weeks of the month and the months. Every cell reads out in full.
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { directionOf, formatRupees } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { compactPaise, digitsOf, monthWords } from './analyticsWords'
import { type Calendar, PAISE_PER_RUPEE, type PeriodRow } from './types'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const
const SECTION = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'

export const TINT: Record<'up' | 'down' | 'flat', string> = {
  up: 'bg-[color:var(--up-tint)]',
  down: 'bg-[color:var(--down-tint)]',
  flat: 'bg-transparent',
}

/** The day rows of the calendar by day number (YYYYMMDD); keys that are not a day are left out. */
export function daysByNumber(days: readonly PeriodRow[]): Map<number, PeriodRow> {
  const out = new Map<number, PeriodRow>()
  for (const row of days) {
    const digits = digitsOf(row.key)
    if (digits.length === 8) out.set(Number(digits), row)
  }
  return out
}

/** The months (YYYYMM) that have a day, newest last. */
export function monthsOf(days: readonly PeriodRow[]): number[] {
  const set = new Set<number>()
  for (const day of daysByNumber(days).keys()) set.add(Math.floor(day / 100))
  return [...set].sort((a, b) => a - b)
}

/** The cells of a month: blanks for the days before the 1st (Monday first), then each day. */
export function monthCells(month: number): Array<number | null> {
  const year = Math.floor(month / 100)
  const m = month % 100
  const first = new Date(Date.UTC(year, m - 1, 1))
  const lead = (first.getUTCDay() + 6) % 7
  const length = new Date(Date.UTC(year, m, 0)).getUTCDate()
  return [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: length }, (_, n) => month * 100 + n + 1),
  ]
}

const MONTH_SHORT = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]
const dayLabel = (day: number) => `${day % 100} ${MONTH_SHORT[(Math.floor(day / 100) % 100) - 1]}`

function Cell({ day, row }: { day: number; row: PeriodRow | undefined }) {
  const net = row?.net_paise ?? 0
  const dir = row ? directionOf(net / PAISE_PER_RUPEE) : 'flat'
  const words = row
    ? `${dayLabel(day)}: net ${formatRupees(net / PAISE_PER_RUPEE, { signed: true })}, ${row.trips} ${row.trips === 1 ? 'trade' : 'trades'}, ${row.wins} won`
    : `${dayLabel(day)}: no trades`
  return (
    <li
      aria-label={words}
      data-testid="calendar-cell"
      data-day={day}
      className={cn(
        'flex min-h-14 flex-col items-center justify-start gap-0.5 rounded-lg border border-[color:var(--divider)] px-0.5 py-1 text-center',
        TINT[dir]
      )}
    >
      <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
        {day % 100}
      </span>
      {row && (
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-[0.75rem] leading-none font-bold',
            TREND_TEXT[dir]
          )}
        >
          <TrendArrow direction={dir} className="size-[0.4375rem]" />
          {compactPaise(net)}
        </span>
      )}
      {row && (
        <span className="text-[0.625rem] font-medium text-[color:var(--text-tertiary)]">
          {row.trips}
        </span>
      )}
    </li>
  )
}

export function CalendarGrid({ calendar }: { calendar: Calendar }) {
  const days = useMemo(() => daysByNumber(calendar.days), [calendar.days])
  const months = useMemo(() => monthsOf(calendar.days), [calendar.days])
  const [picked, setPicked] = useState<number | null>(null)
  const month = picked !== null && months.includes(picked) ? picked : (months.at(-1) ?? null)
  if (month === null) {
    return (
      <section aria-label="Calendar" className={SECTION}>
        <h2 className="text-base font-bold">Calendar</h2>
        <p className="mt-2 text-sm text-[color:var(--text-secondary)]">
          Days with closed trades are shown here.
        </p>
      </section>
    )
  }
  const at = months.indexOf(month)
  const cells = monthCells(month)
  const weeks = calendar.weeks.filter((w) => {
    const digits = digitsOf(w.key)
    return digits.length >= 6 && digits.slice(0, 4) === String(Math.floor(month / 100))
  })
  const inMonth = calendar.months.find((m) => digitsOf(m.key) === String(month))
  return (
    <section aria-label="Calendar" className={`${SECTION} flex flex-col gap-3`}>
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          aria-label="Earlier month"
          disabled={at <= 0}
          onClick={() => setPicked(months[at - 1])}
          className="inline-flex size-12 items-center justify-center rounded-xl hover:bg-accent disabled:opacity-40"
        >
          <ChevronLeft aria-hidden="true" className="size-5" />
        </button>
        <h2 className="text-base font-bold">{monthWords(month)}</h2>
        <button
          type="button"
          aria-label="Later month"
          disabled={at >= months.length - 1}
          onClick={() => setPicked(months[at + 1])}
          className="inline-flex size-12 items-center justify-center rounded-xl hover:bg-accent disabled:opacity-40"
        >
          <ChevronRight aria-hidden="true" className="size-5" />
        </button>
      </div>
      {inMonth && (
        <p className="text-sm font-semibold text-[color:var(--text-secondary)]">
          Month net <Signed paise={inMonth.net_paise} />, {inMonth.trips}{' '}
          {inMonth.trips === 1 ? 'trade' : 'trades'}
        </p>
      )}
      <div>
        <ul
          className="grid grid-cols-7 gap-1 text-center text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]"
          aria-hidden="true"
        >
          {WEEKDAYS.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
        <ul className="mt-1 grid grid-cols-7 gap-1" aria-label={`Days of ${monthWords(month)}`}>
          {cells.map((day, n) =>
            day === null ? (
              <li key={`b${n}`} aria-hidden="true" />
            ) : (
              <Cell key={day} day={day} row={days.get(day)} />
            )
          )}
        </ul>
      </div>
      {weeks.length > 0 && (
        <ul
          className="flex flex-col gap-1 border-t border-[color:var(--divider)] pt-2"
          aria-label="Weeks"
        >
          {weeks.map((w) => (
            <li key={w.key} className="flex items-baseline justify-between text-sm">
              <span className="font-medium text-[color:var(--text-secondary)]">Week {w.key}</span>
              <span className="inline-flex items-baseline gap-3">
                <span className="text-[color:var(--text-tertiary)]">
                  {w.trips} {w.trips === 1 ? 'trade' : 'trades'}
                </span>
                <Signed paise={w.net_paise} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
