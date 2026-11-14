// SUDARSHAN P7 lane G (M7.32): the Concentration card, moving with ticks (design p7 9: "the bars
// move with ticks"). The risk answer carries the rows its bars were worked out from; `useTabLive`
// prices the listed ones from ticks newer than the read (one draw a frame, no polling), and the
// weights, the limit marks and the sentences are worked out again by the service's own rule
// (`concentration.ts`, held to it by vectors/risk.json). The feed line says where the prices
// stand, and "Real-time" shows only while ticks arrive. An answer without those rows (an older
// service) is drawn as the service worked it out, with the time it was read.
import { CircleAlert } from 'lucide-react'
import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { formatAmount } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { MarketOpen } from '@/sudarshan/dashboard/live'
import { clockOfInstant, dayOfInstant } from '../days'
import { LIVE_CAP } from '../liveTab'
import { pctNumber } from '../Signed'
import { dayText } from '../tab/format'
import type { HoldingRow, RiskConcentration, RiskReport, RiskWeight } from '../types'
import { useTabLive } from '../useTabLive'
import { concentration, holdingsOf } from './concentration'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const LABEL = 'text-xs font-semibold text-muted-foreground'
const MEANING = 'text-xs text-[color:var(--text-tertiary)]'
const NO_ROWS: readonly HoldingRow[] = []
/** The sums of a tab that the weights do not read. */
const NOTHING = {
  realised_paise: 0,
  income_paise: 0,
  fees_paise: 0,
  money_in_paise: 0,
  money_out_paise: 0,
  closed_today_paise: 0,
  closed_today_base_paise: 0,
  liabilities_paise: 0,
}

/** "14:05:09 on 14 Nov 2026": the instant a figure was read, to the second. */
export const readWords = (at: number): string =>
  `${clockOfInstant(at)} on ${dayText(dayOfInstant(at))}`

function Bars({ title, rows }: { title: string; rows: readonly RiskWeight[] }) {
  if (rows.length === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {rows.map((row) => {
        const weight = pctNumber(row.weight_pct) ?? 0
        return (
          <div key={row.label} className="flex flex-col gap-0.5">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 break-words">{row.label}</span>
              <span className="shrink-0 font-semibold whitespace-nowrap">
                {formatAmount(weight, 1)}%
              </span>
            </div>
            <div className="h-2 rounded-full bg-[color:var(--divider)]">
              <div
                className={cn(
                  'h-2 rounded-full',
                  row.over_limit ? 'bg-[color:var(--warning-text)]' : 'bg-[color:var(--brand)]'
                )}
                style={{ width: `${Math.min(100, Math.max(0, weight))}%` }}
              />
            </div>
            {row.over_limit && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--warning-text)]">
                <CircleAlert aria-hidden="true" className="size-3.5" />
                Above your limit
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function Concentration({
  report,
  readAt,
  marketOpen,
}: {
  report: RiskReport
  /** When the phone got the answer: the time said for an answer that carries no rows. */
  readAt: number
  marketOpen: MarketOpen
}) {
  // An older service sends no rows: its own bars are shown as read, and nothing is subscribed.
  const source = (report as Partial<RiskReport>).live ?? null
  const live = useTabLive({
    rows: source?.rows ?? NO_ROWS,
    fixed: NOTHING,
    readAt: source?.read_at ?? Number.MAX_SAFE_INTEGER,
    marketOpen,
    enabled: source !== null,
  })
  const shown: RiskConcentration = useMemo(
    () =>
      source === null
        ? report.risk.concentration
        : concentration(holdingsOf(live.rows), source.max_holding_pct, source.max_sector_pct),
    [source, live.rows, report.risk.concentration]
  )
  const untagged = pctNumber(shown.untagged_pct)
  return (
    <section aria-label="Concentration" className={cn(CARD, 'flex flex-col gap-3')}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className={LABEL}>Concentration</h3>
        {source !== null && <LiveLine line={live.line} now={live.now} />}
      </div>
      {shown.holdings.length === 0 && shown.sectors.length === 0 && (
        <p className="text-sm text-[color:var(--text-secondary)]">Nothing is held here yet.</p>
      )}
      <Bars title="Largest holdings" rows={shown.holdings} />
      <Bars title="Largest sectors" rows={shown.sectors} />
      {shown.flags.map((flag) => (
        <p key={flag} className="flex gap-2 text-sm font-semibold text-[color:var(--warning-text)]">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>{flag}</span>
        </p>
      ))}
      {untagged !== null && (
        <p className={MEANING}>
          {formatAmount(untagged, 1)}% has no sector tag and is in no sector bar.
        </p>
      )}
      <p className={MEANING}>
        {source === null
          ? `As of ${readWords(readAt)}, when this view was read. These bars do not move with prices; read again for newer values.`
          : `Listed shares are priced from the feed and the bars follow each tick. Funds and assets with a typed value, and every share until its first tick, are as read at ${readWords(source.read_at)}.`}
        {live.capped > 0 &&
          ` ${live.capped} holdings past the first ${LIVE_CAP} keep the price of that read.`}
      </p>
    </section>
  )
}
