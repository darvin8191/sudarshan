// SUDARSHAN M14.3, JD4: the calendar. A month grid in which every day with trades carries its net
// as text with its sign, a small arrow and a tint by sign (the tint is the third sign, never the
// only one), plus the weeks of the month and the months. Every cell reads out in full.
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { directionOf, formatRupees } from '@/sudarshan/dashboard/format'
import { TREND_TEXT } from '@/sudarshan/dashboard/Trend'
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

/** The ISO week a day (YYYYMMDD) falls in, as the service keys weeks: year and week number. */
export function isoWeekOf(day: number): { year: number; week: number } {
  const date = new Date(
    Date.UTC(Math.floor(day / 10000), (Math.floor(day / 100) % 100) - 1, day % 100)
  )
  const weekday = (date.getUTCDay() + 6) % 7 // Monday 0
  date.setUTCDate(date.getUTCDate() - weekday + 3) // the Thursday of that week names its year
  const year = date.getUTCFullYear()
  const firstThursday = new Date(Date.UTC(year, 0, 4))
  const offset = (firstThursday.getUTCDay() + 6) % 7
  firstThursday.setUTCDate(firstThursday.getUTCDate() - offset + 3)
  return { year, week: 1 + Math.round((date.getTime() - firstThursday.getTime()) / 604_800_000) }
}

/** A week key ("2026W45", "2026-W45") as year and week; null for one this page cannot read. */
export function weekOfKey(key: string): { year: number; week: number } | null {
  const m = /^(\d{4})-?W(\d{1,2})$/.exec(key.trim())
  return m ? { year: Number(m[1]), week: Number(m[2]) } : null
}

/**
 * The weeks to list under a month (review M8): only those holding one of its days, each named
 * "Week 45" (the month's heading carries the year); a key this page cannot read is left out
 * rather than shown as it is.
 */
export function weeksOfMonth(
  weeks: readonly PeriodRow[],
  month: number
): Array<{ row: PeriodRow; week: number }> {
  const held = new Set(
    monthCells(month)
      .filter((d): d is number => d !== null)
      .map((d) => {
        const w = isoWeekOf(d)
        return `${w.year}:${w.week}`
      })
  )
  return weeks.flatMap((row) => {
    const w = weekOfKey(row.key)
    return w && held.has(`${w.year}:${w.week}`) ? [{ row, week: w.week }] : []
  })
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
        'flex min-h-14 min-w-0 flex-col items-center justify-start gap-0.5 overflow-hidden rounded-lg border border-[color:var(--divider)] px-0.5 py-1 text-center',
        TINT[dir]
      )}
    >
      <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
        {day % 100}
      </span>
      {row && (
        // Review M4: no arrow inside a cell this small; the sign and the tint still carry the
        // direction, and the figure is at most five characters, so it never runs into the next.
        <span
          className={cn(
            'text-[0.6875rem] leading-none font-bold whitespace-nowrap',
            TREND_TEXT[dir]
          )}
        >
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
  const weeks = weeksOfMonth(calendar.weeks, month)
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
          {weeks.map(({ row, week }) => (
            <li
              key={row.key}
              className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm"
            >
              <span className="font-medium text-[color:var(--text-secondary)]">Week {week}</span>
              <span className="ml-auto inline-flex items-baseline gap-3">
                <span className="text-[color:var(--text-tertiary)]">
                  {row.trips} {row.trips === 1 ? 'trade' : 'trades'}
                </span>
                <Signed paise={row.net_paise} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
