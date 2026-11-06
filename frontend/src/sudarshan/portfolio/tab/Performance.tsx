// SUDARSHAN M7.30, M7.31, M7.38: widget 7 of the Overview and the Performance part (design 4.6
// and 4.7): the return (XIRR once a year of money is behind it, the absolute return before), the
// time-weighted return with its start date, the same money put into NIFTY 50 and NIFTY 500
// ("Dividends not included"), and each financial year's realised gain, income and fees. A figure
// the service cannot give is a sentence, never a zero.
import { cn } from '@/lib/utils'
import { directionOf, formatPercent } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { pctNumber, Signed } from '../Signed'
import type { BenchmarkFigure, PerformanceReport } from '../types'
import { dayText, rupeesText } from './format'
import { UnpricedNote } from './SummaryTiles'

const SECTION = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const LABEL = 'text-xs font-semibold text-muted-foreground'
const NOTE = 'text-[0.78125rem] font-medium text-[color:var(--text-secondary)]'

export const INDEX_WAITING =
  'NIFTY 50 and NIFTY 500 history is still being collected, a little each day, so that it never slows your live prices. The comparison appears when it is ready.'

/** A rate with its sign and arrow as well as its colour. */
function Rate({ pct }: { pct: string }) {
  const n = pctNumber(pct)
  if (n === null) return null
  const direction = directionOf(n)
  return (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap', TREND_TEXT[direction])}>
      <TrendArrow direction={direction} className="size-[0.5625rem]" />
      {formatPercent(n)}
    </span>
  )
}

function Figure({
  label,
  children,
  note,
}: {
  label: string
  children: React.ReactNode
  note?: string | null
}) {
  return (
    <div className="flex min-w-0 flex-col gap-px">
      <dt className={LABEL}>{label}</dt>
      <dd className="text-[0.9375rem] font-semibold">{children}</dd>
      {note && <dd className={NOTE}>{note}</dd>}
    </div>
  )
}

function Benchmark({ b, report }: { b: BenchmarkFigure; report: PerformanceReport }) {
  const has = b.xirr_pct !== null || b.twr_pct !== null || b.value_paise !== null
  return (
    <li className="flex flex-col gap-1 border-t border-[color:var(--divider)] pt-2 first:border-t-0 first:pt-0">
      <p className="text-sm font-bold">{b.label}</p>
      {has && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
          {b.value_paise !== null && (
            <Figure label="Your money in the index">
              {rupeesText(b.value_paise)}
              <span className="block text-xs font-medium text-[color:var(--text-secondary)]">
                You have {rupeesText(report.value_paise)}
              </span>
            </Figure>
          )}
          {b.xirr_pct !== null && (
            <Figure label="Yearly return (XIRR)">
              <Rate pct={b.xirr_pct} />
            </Figure>
          )}
          {b.twr_pct !== null && report.twr.start_day !== null && (
            <Figure label={`Since ${dayText(report.twr.start_day)}`}>
              <Rate pct={b.twr_pct} />
            </Figure>
          )}
        </dl>
      )}
      {b.words.map((w) => (
        <p key={w} className={NOTE}>
          {w}
        </p>
      ))}
    </li>
  )
}

export function Performance({
  report,
  unpriced = 0,
}: {
  report: PerformanceReport
  /** Holdings with no price yet, counted at cost in the value the return rests on. */
  unpriced?: number
}) {
  const x = report.xirr
  const t = report.twr
  const ab = pctNumber(report.absolute_return_pct)
  const noIndex =
    report.benchmarks.length === 0 ||
    report.benchmarks.every(
      (b) => b.value_paise === null && b.twr_pct === null && b.xirr_pct === null
    )
  return (
    <section aria-label="Performance" className={`${SECTION} flex flex-col gap-3`}>
      <h2 className="text-base font-bold">Return</h2>
      <UnpricedNote count={unpriced} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-3">
        <Figure label="Gain so far">
          <Signed paise={report.total_gain_paise} pct={report.absolute_return_pct} />
        </Figure>
        <Figure label="Net money put in">{rupeesText(report.net_put_in_paise)}</Figure>
        {x.state === 'shown' && x.pct !== null ? (
          <Figure label="Yearly return (XIRR)">
            <Rate pct={x.pct} />
          </Figure>
        ) : (
          <Figure
            label="Return"
            note={
              x.words ??
              (x.from_day !== null
                ? `XIRR appears after one year (from ${dayText(x.from_day)}).`
                : null)
            }
          >
            {ab === null ? 'Not known yet' : <Rate pct={String(ab)} />}
            {ab !== null && (
              <span className="block text-xs font-medium text-[color:var(--text-secondary)]">
                Absolute return
              </span>
            )}
          </Figure>
        )}
        <Figure
          label={t.start_day !== null ? `TWR since ${dayText(t.start_day)}` : 'TWR'}
          note={t.pct === null ? t.words : null}
        >
          {t.pct === null ? 'Not known yet' : <Rate pct={t.pct} />}
          {t.annualised_pct !== null && (
            <span className="block text-xs font-medium text-[color:var(--text-secondary)]">
              {formatPercent(Number(t.annualised_pct))} a year
            </span>
          )}
        </Figure>
      </dl>

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-bold">Against the indices</h3>
        <p className={NOTE}>
          The same money on the same days, put into each index. Dividends not included.
        </p>
        {noIndex && <p className="text-sm text-[color:var(--text-secondary)]">{INDEX_WAITING}</p>}
        <ul className="flex flex-col gap-2">
          {report.benchmarks.map((b) => (
            <Benchmark key={b.benchmark} b={b} report={report} />
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-bold">Each financial year</h3>
        {report.by_fy.length === 0 ? (
          <p className="text-sm text-[color:var(--text-secondary)]">
            Nothing realised, earned or paid yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="border-b border-[color:var(--divider)]">
                  <th
                    scope="col"
                    className="py-1.5 pr-3 text-left text-xs font-semibold text-muted-foreground"
                  >
                    Year
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-1.5 text-left text-xs font-semibold text-muted-foreground"
                  >
                    Realised gain
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-1.5 text-left text-xs font-semibold text-muted-foreground"
                  >
                    Income
                  </th>
                  <th
                    scope="col"
                    className="py-1.5 pl-3 text-left text-xs font-semibold text-muted-foreground"
                  >
                    Fees
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[color:var(--divider)]">
                {report.by_fy.map((f) => (
                  <tr key={f.fy} className="min-h-12 align-top">
                    <th scope="row" className="py-2.5 pr-3 text-left font-bold">
                      {f.label}
                    </th>
                    <td className="px-3 py-2.5">
                      <Signed paise={f.realised_paise} />
                    </td>
                    <td className="px-3 py-2.5">{rupeesText(f.income_paise)}</td>
                    <td className="py-2.5 pl-3">{rupeesText(f.fees_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}
