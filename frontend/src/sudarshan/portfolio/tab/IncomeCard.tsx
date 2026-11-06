// SUDARSHAN M7.38: widget 8 of the Overview (design 4.6): income (dividends and interest) of this
// financial year and of each of the last twelve months, as horizontal bars with the amount written
// on each, so no month is told by length alone.
import type { PerformanceReport } from '../types'
import { rupeesText } from './format'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** 202611 to "Nov 2026". */
export function monthText(month: number): string {
  return `${MONTHS[(month % 100) - 1] ?? '?'} ${Math.floor(month / 100)}`
}

export function IncomeCard({ report }: { report: PerformanceReport }) {
  const { this_fy_paise, by_month } = report.income
  const top = Math.max(0, ...by_month.map((m) => m.paise))
  const none = by_month.every((m) => m.paise === 0)
  return (
    <section
      aria-label="Income"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 className="text-base font-bold">Income</h2>
      <dl className="flex flex-col gap-px">
        <dt className="text-xs font-semibold text-muted-foreground">This financial year</dt>
        <dd className="sd-display text-[1.5rem] leading-[1.1] font-semibold">
          {rupeesText(this_fy_paise)}
        </dd>
      </dl>
      {none ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          No dividend or interest in the last 12 months.
        </p>
      ) : (
        <ul aria-label="Income by month" className="flex flex-col gap-1.5">
          {by_month.map((m) => (
            <li key={m.month} className="grid grid-cols-[4.5rem_1fr] items-center gap-2 text-sm">
              <span className="text-[color:var(--text-secondary)]">{monthText(m.month)}</span>
              <span className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden="true"
                  className="h-3 rounded-sm bg-[color:var(--brand)]"
                  style={{
                    width: top > 0 && m.paise > 0 ? `${Math.max(2, (m.paise / top) * 70)}%` : '0%',
                  }}
                />
                <span className="font-semibold whitespace-nowrap">{rupeesText(m.paise)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
