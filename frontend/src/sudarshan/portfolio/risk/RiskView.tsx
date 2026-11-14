// SUDARSHAN P7 F7 (M7.32): the Risk view, `/portfolio?view=risk` (design p7 9.3): every custom tab
// together or one of them. Largest fall, volatility and the three ratios are statistics on closing
// values and say which day they reach; each has one plain line on what it means, and where the
// service gives no figure its sentence stands in the figure's place, never a zero. The
// concentration bars move with ticks (`Concentration.tsx`, lane G). The emergency fund is worked
// out by the service on the values at the moment the view was read, and says when. Owner's page
// only.
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Settings2 } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { formatAmount, formatPercent } from '@/sudarshan/dashboard/format'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import type { MarketOpen } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { getRisk, PortfolioError, problemOf } from '../api'
import { pctNumber } from '../Signed'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { dayText } from '../tab/format'
import type { RiskDrawdown, RiskReport, RiskStat, TabInfo } from '../types'
import { Concentration, readWords } from './Concentration'
import { SettingsSheet } from './SettingsSheet'

export const RISK_KEY = [...PORTFOLIO_KEY, 'risk'] as const
/** The view's own sentence while its switch is off on the server (the op answers `not_ready`). */
export const RISK_OFF =
  'The risk view is switched off on the server. It opens here once a release switches it on.'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const LABEL = 'text-xs font-semibold text-muted-foreground'
const MEANING = 'text-xs text-[color:var(--text-tertiary)]'
const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-4 text-[0.9375rem] font-semibold text-[color:var(--brand)] outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

const STATS: { key: 'volatility' | 'sharpe' | 'sortino' | 'calmar'; label: string; meaning: string; pct: boolean }[] = [
  { key: 'volatility', label: 'Volatility', meaning: 'How much the value swings in a year. Lower is steadier.', pct: true },
  { key: 'sharpe', label: 'Sharpe', meaning: 'Return above the risk-free rate for each unit of swing. Higher is better.', pct: false },
  { key: 'sortino', label: 'Sortino', meaning: 'The same, counting only the swings downward. Higher is better.', pct: false },
  { key: 'calmar', label: 'Calmar', meaning: 'Yearly return against the largest fall. Higher is better.', pct: false },
] // biome-ignore format: one row per figure

/** A figure of four decimals as the phone shows it: two, a true minus, a percent sign if it is one. */
export function statText(value: string | null, pct: boolean): string | null {
  const n = pctNumber(value)
  if (n === null) return null
  if (pct) return formatPercent(n).replace(/^\+/, '')
  return `${Number(n.toFixed(2)) < 0 ? '−' : ''}${formatAmount(n, 2)}`
}

/** "from 12 Aug 2026 to 3 Sep 2026, recovered 20 Oct 2026", or "not yet recovered". */
export function fallWords(fall: RiskDrawdown): string | null {
  if (fall.peak_day === null || fall.trough_day === null) return null
  const span = `From ${dayText(fall.peak_day)} to ${dayText(fall.trough_day)}`
  return fall.recovered_day === null
    ? `${span}, not yet recovered`
    : `${span}, recovered ${dayText(fall.recovered_day)}`
}

function Stat({
  label,
  meaning,
  stat,
  pct,
}: {
  label: string
  meaning: string
  stat: RiskStat
  pct: boolean
}) {
  const text = statText(stat.value, pct)
  return (
    <div className="flex flex-col gap-0.5 border-t border-[color:var(--divider)] py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold">{label}</span>
        {text !== null && (
          <span className="shrink-0 text-[0.9375rem] font-bold whitespace-nowrap">{text}</span>
        )}
      </div>
      <span className={MEANING}>{meaning}</span>
      {text === null && stat.note && (
        <span className="text-sm text-[color:var(--text-secondary)]">{stat.note}</span>
      )}
    </div>
  )
}

function Figures({
  report,
  tabs,
  readAt,
  marketOpen,
}: {
  report: RiskReport
  tabs: readonly TabInfo[]
  readAt: number
  marketOpen: MarketOpen
}) {
  const { risk } = report
  const [other, setOther] = useState<number | null>(null)
  const fall = statText(risk.drawdown.pct, true)
  const shared = risk.overlap.find((o) => o.tab_id === other) ?? risk.overlap[0]
  const months = pctNumber(risk.emergency.months)
  return (
    <>
      <section aria-label="Largest fall" className={cn(CARD, 'flex flex-col gap-1')}>
        <h3 className={LABEL}>Largest fall</h3>
        {fall !== null && (
          <span className="sd-display text-[1.75rem] leading-tight font-bold">{fall}</span>
        )}
        <span className={MEANING}>The deepest drop from a high to the low after it.</span>
        {fallWords(risk.drawdown) && (
          <span className="text-sm text-[color:var(--text-secondary)]">
            {fallWords(risk.drawdown)}
          </span>
        )}
        {risk.drawdown.note && (
          <span className="text-sm text-[color:var(--text-secondary)]">{risk.drawdown.note}</span>
        )}
      </section>
      <section aria-label="Swings and ratios" className={cn(CARD, 'flex flex-col')}>
        {STATS.map((s) => (
          <Stat key={s.key} label={s.label} meaning={s.meaning} stat={risk[s.key]} pct={s.pct} />
        ))}
        <p className={cn(MEANING, 'pt-1')}>
          {report.basis} {risk.days > 0 && `Rests on ${risk.days} trading days.`}
        </p>
      </section>
      <Concentration report={report} readAt={readAt} marketOpen={marketOpen} />
      {risk.overlap.length > 0 && shared && (
        <section aria-label="Overlap" className={cn(CARD, 'flex flex-col gap-2')}>
          <h3 className={LABEL}>Overlap with another tab</h3>
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-semibold text-[color:var(--text-secondary)]">
              Compare with
            </span>
            <select
              className="min-h-12 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 text-[0.9375rem]"
              value={shared.tab_id}
              onChange={(e) => setOther(Number(e.target.value))}
            >
              {risk.overlap.map((o) => (
                <option key={o.tab_id} value={o.tab_id}>
                  {tabs.find((t) => t.id === o.tab_id)?.name ?? 'Another tab'}
                </option>
              ))}
            </select>
          </label>
          <p className="text-[0.9375rem]">
            <span className="font-bold">{formatAmount(pctNumber(shared.weight_pct) ?? 0, 1)}%</span>{' '}
            of this tab is also held there.
          </p>
          {shared.names.length > 0 && (
            <p className="text-sm text-[color:var(--text-secondary)]">
              Held in both: {shared.names.join(', ')}
            </p>
          )}
        </section>
      )}
      <section aria-label="Emergency fund" className={cn(CARD, 'flex flex-col gap-1.5')}>
        <h3 className={LABEL}>Emergency fund</h3>
        {months !== null && (
          <>
            <span className="sd-display text-[1.75rem] leading-tight font-bold">
              {formatAmount(months, 1)} months
            </span>
            <div className="h-2 rounded-full bg-[color:var(--divider)]">
              <div
                className="h-2 rounded-full bg-[color:var(--brand)]"
                style={{
                  width: `${Math.min(100, (months / risk.emergency.target_months) * 100)}%`,
                }}
              />
            </div>
            <span className={MEANING}>
              Of your expenses covered; your target is {risk.emergency.target_months} months. As of{' '}
              {readWords(report.live?.read_at ?? readAt)}, when this view was read.
            </span>
          </>
        )}
        {risk.emergency.note && (
          <span className="text-sm text-[color:var(--text-secondary)]">{risk.emergency.note}</span>
        )}
      </section>
    </>
  )
}

export function RiskView({ tabs }: { tabs: readonly TabInfo[] }) {
  const [tab, setTab] = useState<number | null>(null)
  const [settings, setSettings] = useState(false)
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const chosen = tab !== null && tabs.some((t) => t.id === tab) ? tab : null
  const query = useQuery({
    queryKey: [...RISK_KEY, 'report', chosen] as const,
    queryFn: async ({ signal }) => {
      const result = await getRisk(chosen === null ? {} : { tab: chosen }, signal)
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  })
  const problem = query.error ? problemOf(query.error) : null
  return (
    <div className="flex flex-col gap-3">
      <MarketCalendarSource key={day} onChange={setMarket} />
      <div className="flex items-center justify-between gap-3">
        <h2 className="sd-display text-[1.375rem] leading-tight font-bold">Risk</h2>
        <span className="flex gap-2">
          <button type="button" className={BUTTON} onClick={() => void query.refetch()}>
            <RefreshCw aria-hidden="true" className="size-4" />
            Read again
          </button>
          <button
            type="button"
            className={BUTTON}
            aria-label="Risk settings"
            onClick={() => setSettings(true)}
          >
            <Settings2 aria-hidden="true" className="size-5" />
          </button>
        </span>
      </div>
      {tabs.length > 0 && (
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-semibold text-[color:var(--text-secondary)]">Tab</span>
          <select
            className="min-h-12 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 text-[0.9375rem]"
            value={chosen ?? ''}
            onChange={(e) => setTab(e.target.value === '' ? null : Number(e.target.value))}
          >
            <option value="">All your tabs together</option>
            {tabs.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {problem !== null ? (
        <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')} aria-live="polite">
          {problem.code === 'not_ready' ? RISK_OFF : problem.message}
        </p>
      ) : query.data === undefined ? (
        <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')} aria-live="polite">
          Working out the risk figures.
        </p>
      ) : (
        <Figures report={query.data} tabs={tabs} readAt={query.dataUpdatedAt} marketOpen={isOpen} />
      )}
      <SettingsSheet
        open={settings}
        onClose={() => setSettings(false)}
        onSaved={() => {
          setSettings(false)
          void query.refetch()
        }}
      />
    </div>
  )
}
