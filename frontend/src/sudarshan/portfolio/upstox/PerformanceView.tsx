// SUDARSHAN M7.15: the return of the Upstox account's shares. The money is Upstox's own trade
// history and the value a closing record, so every figure here is as of that record and says so;
// only "Holdings value now" moves with ticks. A share with no purchase in the history is listed
// as not counted, never shown as bought for nothing. Read-only.
import { Clock, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { pctNumber, Signed } from '../Signed'
import { dayText, quantityText, rupeesText } from '../tab/format'
import { Benchmark, INDEX_WAITING, Rate } from '../tab/Performance'
import type { UpstoxHolding, UpstoxPart, UpstoxPerformance, UpstoxReturn } from '../types'
import { holdingNow, holdingsTotals } from './live'
import { CARD, CHIP, Figure, NoFigures, ReadStamp } from './parts'
import { instrumentOf, type UpstoxLive } from './useUpstoxLive'

const LABEL = 'text-xs font-semibold text-muted-foreground'
const NOTE = 'text-[0.78125rem] font-medium text-[color:var(--text-secondary)]'

/** "At the close of 23 Oct 2026, not real-time": what every return figure rests on. */
export function closeWords(day: number): string {
  return `At the close of ${dayText(day)}, not real-time`
}

function Small({
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

/** The holdings' value at the latest tick: the one real-time figure of this view. */
function ValueNow({
  part,
  live,
}: {
  part: UpstoxPart<UpstoxHolding[]> | undefined
  live: UpstoxLive
}) {
  const rows = part?.data
  if (!rows || rows.length === 0) return null
  const now = rows.map((row) => {
    const key = instrumentOf(row)
    return holdingNow(row, key ? live.ticks.get(key) : undefined, part.read_at)
  })
  const totals = holdingsTotals(now)
  const allStale = live.line.kind === 'delayed' && live.line.stale
  return (
    <section
      aria-label="Holdings value now"
      className={cn(CARD, 'flex flex-col gap-2 px-4 py-3.5')}
    >
      <dl>
        <Figure label="Holdings value now">
          <span className={cn(allStale && 'text-muted-foreground')}>
            {rupeesText(totals.valuePaise)}
          </span>
        </Figure>
      </dl>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LiveLine line={live.line} now={live.now} />
        <ReadStamp at={part.read_at} />
      </div>
    </section>
  )
}

function Return({ report, got }: { report: UpstoxPerformance; got: UpstoxReturn }) {
  const x = got.xirr
  const t = got.twr
  const ab = pctNumber(got.absolute_return_pct)
  const noIndex = got.benchmarks.every(
    (b) => b.value_paise === null && b.twr_pct === null && b.xirr_pct === null
  )
  return (
    <section aria-label="Return" className={cn(CARD, 'flex flex-col gap-3 px-4 py-3.5')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold">Return</h2>
        <span className={CHIP}>
          <Clock aria-hidden="true" className="mr-1 size-3.5" />
          Closing record
        </span>
      </div>
      <p className="text-xs font-semibold text-[color:var(--text-tertiary)]">
        {closeWords(got.valuation_day)}
        {report.from !== null && ` · trades since ${dayText(report.from)}`}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-3">
        <Small label="Value counted">{rupeesText(got.value_paise)}</Small>
        <Small label="Net money put in">{rupeesText(got.net_put_in_paise)}</Small>
        <Small label="Gain so far">
          <Signed paise={got.total_gain_paise} pct={got.absolute_return_pct} />
        </Small>
        {x.state === 'shown' && x.pct !== null ? (
          <Small label="Yearly return (XIRR)">
            <Rate pct={x.pct} />
          </Small>
        ) : (
          <Small label="Return" note={x.words}>
            {ab === null ? 'Not known yet' : <Rate pct={String(ab)} />}
            {ab !== null && (
              <span className="block text-xs font-medium text-[color:var(--text-secondary)]">
                Absolute return
              </span>
            )}
          </Small>
        )}
        <Small
          label={t.start_day !== null ? `TWR since ${dayText(t.start_day)}` : 'TWR'}
          note={t.pct === null ? t.words : 'All holdings'}
        >
          {t.pct === null ? 'Not known yet' : <Rate pct={t.pct} />}
          {t.annualised_pct !== null && (
            <span className="block text-xs font-medium text-[color:var(--text-secondary)]">
              {formatPercent(Number(t.annualised_pct))} a year
            </span>
          )}
        </Small>
      </dl>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-bold">Against the indices</h3>
        <p className={NOTE}>
          The same money on the same days, put into each index. Dividends not included.
        </p>
        {noIndex && <p className="text-sm text-[color:var(--text-secondary)]">{INDEX_WAITING}</p>}
        <ul className="flex flex-col gap-2">
          {got.benchmarks.map((b) => (
            <Benchmark key={b.benchmark} b={b} report={got} />
          ))}
        </ul>
      </div>
    </section>
  )
}

export function PerformanceView({
  report,
  part,
  live,
}: {
  report: UpstoxPerformance
  part: UpstoxPart<UpstoxHolding[]> | undefined
  live: UpstoxLive
}) {
  const got = report.data
  return (
    <div className="flex flex-col gap-3">
      <ValueNow part={part} live={live} />
      {report.state !== 'ok' || got === null ? (
        <NoFigures part={undefined}>
          {report.note ?? 'The return could not be worked out yet.'}
        </NoFigures>
      ) : (
        <Return report={report} got={got} />
      )}
      {report.uncounted.length > 0 && (
        <section aria-label="Not counted" className={cn(CARD, 'flex flex-col gap-2 px-4 py-3.5')}>
          <h2 className="text-base font-bold">Not counted</h2>
          <p className={NOTE}>
            Held, with no purchase in the trade history read. Worth{' '}
            {rupeesText(report.uncounted_value_paise)} at that close; left out of the return, not
            taken as bought for nothing.
          </p>
          <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
            {report.uncounted.map((u) => (
              <li
                key={u.isin}
                className="flex min-h-12 items-center justify-between gap-3 py-2 text-sm"
              >
                <span className="min-w-0">
                  <span className="block truncate font-bold">{u.symbol ?? u.name}</span>
                  <span className="block text-xs text-[color:var(--text-secondary)]">
                    {quantityText(u.qty_u)} shares
                  </span>
                </span>
                <span className="shrink-0 font-semibold">{rupeesText(u.value_paise)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {report.words.length > 0 && (
        <ul aria-label="What these figures count" className="flex flex-col gap-2">
          {report.words.map((w) => (
            <li
              key={w}
              className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
            >
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {w}
            </li>
          ))}
        </ul>
      )}
      <ReadStamp at={report.read_at} />
    </div>
  )
}
