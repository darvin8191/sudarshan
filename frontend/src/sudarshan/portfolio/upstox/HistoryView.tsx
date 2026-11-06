// SUDARSHAN M7.4: the Upstox account at each close: value against what went in, then the last
// 10 days. These are closing records taken at 15:55, not real-time figures, and they say so.
import { ChevronRight, Clock } from 'lucide-react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { Signed } from '../Signed'
import { dayText, rupeesText } from '../tab/format'
import type { UpstoxEodDay, UpstoxHistory } from '../types'
import { CARD, CHIP, LINK, NoFigures, ReadStamp } from './parts'

const LAST_DAYS = 10
const W = 320
const H = 120
const PAD = 6

/** A day whose holdings were read: the only kind the chart draws. */
type ReadDay = UpstoxEodDay & { holdings_value_paise: number; holdings_cost_paise: number }
const isRead = (d: UpstoxEodDay): d is ReadDay =>
  d.holdings_value_paise !== null && d.holdings_cost_paise !== null

export const NOT_RECORDED = 'Not recorded at the close'

/** Value (solid) against invested (dashed) as two lines; the legend says which is which. */
function ValueChart({ days }: { days: ReadDay[] }) {
  const values = days.flatMap((d) => [d.holdings_value_paise, d.holdings_cost_paise])
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = high - low || 1
  const x = (n: number) =>
    PAD + (days.length < 2 ? (W - 2 * PAD) / 2 : (n * (W - 2 * PAD)) / (days.length - 1))
  const y = (v: number) => H - PAD - ((v - low) / span) * (H - 2 * PAD)
  const line = (pick: (d: ReadDay) => number) =>
    days.map((d, n) => `${x(n)},${y(pick(d))}`).join(' ')
  const first = days[0]
  const last = days[days.length - 1]
  return (
    <figure className={cn(CARD, 'flex flex-col gap-2 px-4 py-3')}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Value of your holdings against what you put in, ${dayText(first.day)} to ${dayText(last.day)}. Now ${rupeesText(last.holdings_value_paise)} against ${rupeesText(last.holdings_cost_paise)} invested.`}
        className="h-auto w-full"
      >
        <polyline
          points={line((d) => d.holdings_cost_paise)}
          fill="none"
          stroke="var(--text-tertiary)"
          strokeWidth="2"
          strokeDasharray="5 4"
        />
        <polyline
          points={line((d) => d.holdings_value_paise)}
          fill="none"
          stroke="var(--brand)"
          strokeWidth="2.5"
        />
      </svg>
      <figcaption className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-semibold text-[color:var(--text-secondary)]">
        <span>Solid line: value</span>
        <span>Dashed line: invested</span>
        <span>
          {dayText(first.day)} to {dayText(last.day)}
        </span>
      </figcaption>
    </figure>
  )
}

/** A part that was not read: the service's reason, or "Not recorded at the close"; never a zero. */
function Unread({ words }: { words?: string | null }) {
  return (
    <span className="font-medium text-[color:var(--text-secondary)]">{words || NOT_RECORDED}</span>
  )
}

export function HistoryView({ history }: { history: UpstoxHistory }) {
  const days = [...history.days].sort((a, b) => a.day - b.day)
  if (days.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <NoFigures part={undefined}>
          {history.note ??
            'No closing records yet. The first one is taken at 15:55 on a trading day.'}
        </NoFigures>
        <Link to="/portfolio-analyzer" className={LINK}>
          Portfolio Analyzer
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>
    )
  }
  const read = days.filter(isRead)
  const recent = days.slice(-LAST_DAYS).reverse()
  const newest = days[days.length - 1]
  return (
    <div className="flex flex-col gap-3">
      {history.note && (
        <p
          aria-live="polite"
          className="rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
        >
          {history.note}
        </p>
      )}
      <p className="flex items-center gap-1.5 text-xs font-semibold text-[color:var(--text-tertiary)]">
        <Clock aria-hidden="true" className="size-3.5" />
        Closing records taken at 15:55, not real-time
      </p>
      {read.length >= 2 && <ValueChart days={read} />}
      {read.length < days.length && (
        <p className="text-xs text-[color:var(--text-tertiary)]">
          A day whose holdings were not read is left out of the chart.
        </p>
      )}
      <h2 className="text-[0.9375rem] font-bold">Last {Math.min(LAST_DAYS, days.length)} days</h2>
      <ul aria-label="Last days" className="flex flex-col gap-2">
        {recent.map((d) => (
          <li key={d.day} className={cn(CARD, 'flex flex-col gap-2 px-4 py-3')}>
            <div className="flex items-center justify-between gap-2">
              <p className="text-base font-bold">{dayText(d.day)}</p>
              <span className="flex gap-1.5">
                {d.provisional && <span className={CHIP}>Provisional</span>}
                {d.late && <span className={CHIP}>Taken late</span>}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <dt className="text-xs font-semibold text-muted-foreground">Value</dt>
                <dd className="font-semibold">
                  {d.holdings_value_paise === null ? (
                    <Unread words={d.holdings_words} />
                  ) : (
                    rupeesText(d.holdings_value_paise)
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-semibold text-muted-foreground">Invested</dt>
                <dd className="font-semibold">
                  {d.holdings_cost_paise === null ? (
                    <Unread words={d.holdings_words} />
                  ) : (
                    rupeesText(d.holdings_cost_paise)
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-semibold text-muted-foreground">Holdings gain</dt>
                <dd className="font-semibold">
                  {d.holdings_value_paise === null || d.holdings_cost_paise === null ? (
                    <Unread words={d.holdings_words} />
                  ) : (
                    <Signed paise={d.holdings_value_paise - d.holdings_cost_paise} />
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-semibold text-muted-foreground">Positions P&amp;L</dt>
                <dd className="font-semibold">
                  {d.positions_pnl_paise === null ? (
                    <Unread words={d.positions_words} />
                  ) : (
                    <Signed paise={d.positions_pnl_paise} />
                  )}
                </dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>
      <ReadStamp at={newest.read_at} />
      <Link to="/portfolio-analyzer" className={LINK}>
        Portfolio Analyzer
        <ChevronRight aria-hidden="true" className="size-4" />
      </Link>
    </div>
  )
}
