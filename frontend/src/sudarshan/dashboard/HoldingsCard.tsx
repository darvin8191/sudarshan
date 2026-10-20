import { ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { tradingApi } from '@/api/trading'
import { cn } from '@/lib/utils'
import { useBrokerStore } from '@/stores/brokerStore'
import { onModeChange } from '@/stores/themeStore'
import type { PortfolioStats } from '@/types/trading'
import { directionOf, formatPercent, formatRupees, toNumber } from './format'
import { TREND_TEXT, TrendArrow } from './Trend'

interface Summary {
  count: number
  current: number
  invested: number
  pnl: number
  percent: number
}

function summarise(
  holdings: unknown[] | undefined,
  stats: PortfolioStats | undefined
): Summary | null {
  const count = holdings?.length ?? 0
  if (!stats || count === 0) return null
  const current = toNumber(stats.totalholdingvalue)
  const invested = toNumber(stats.totalinvvalue)
  const pnl = toNumber(stats.totalprofitandloss)
  const percent = toNumber(stats.totalpnlpercentage)
  if (current === null || invested === null || pnl === null || percent === null) return null
  return { count, current, invested, pnl, percent }
}

/**
 * The holdings at a glance: what they are worth now, what went into them and
 * the overall P&L, from one holdings call when the dashboard opens (and again
 * when the mode changes), not on a timer. Nothing is shown when there are no
 * holdings, when the broker has none to give (crypto), when there is no API
 * key to ask with, or when the call fails: the Holdings page has the detail,
 * and its own errors. There is no "today" figure, because the holdings call
 * carries no day change.
 */
export function HoldingsCard({ apiKey }: { apiKey: string | null }) {
  const supported = useBrokerStore((s) => s.capabilities?.broker_type !== 'crypto')
  const [summary, setSummary] = useState<Summary | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    if (!apiKey) return
    try {
      const response = await tradingApi.getHoldings(apiKey)
      if (!alive.current) return
      setSummary(
        response.status === 'success'
          ? summarise(response.data?.holdings, response.data?.statistics)
          : null
      )
    } catch {
      if (alive.current) setSummary(null)
    }
  }, [apiKey])

  useEffect(() => {
    if (supported) load()
  }, [supported, load])

  // Live and Analyze hold different books.
  useEffect(() => onModeChange(() => load()), [load])

  if (!supported || !summary) return null

  const direction = directionOf(summary.pnl)

  return (
    <section
      aria-labelledby="sd-holdings-title"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="sd-holdings-title" className="flex items-center gap-2 text-[0.9375rem] font-bold">
          Holdings
          <span className="flex h-6 items-center rounded-full bg-[color:var(--brand-tint)] px-2.5 text-xs font-semibold text-[color:var(--brand)]">
            {summary.count}
          </span>
        </h2>
        <Link
          to="/holdings"
          className="-my-2 -mr-2 inline-flex min-h-12 items-center gap-0.5 rounded-lg px-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          See all
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>
      <dl className="grid grid-cols-2 gap-3">
        <div className="flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Current value</dt>
          <dd className="sd-display text-[1.3125rem] leading-[1.15] font-semibold">
            {formatRupees(summary.current, { decimals: 0 })}
          </dd>
          <dd className="text-[0.71875rem] font-medium whitespace-nowrap text-[color:var(--text-tertiary)]">
            Invested {formatRupees(summary.invested, { decimals: 0 })}
          </dd>
        </div>
        <div className="flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Overall P&amp;L</dt>
          <dd
            className={cn(
              'sd-display flex items-center gap-1 text-[1.3125rem] leading-[1.15] font-semibold',
              TREND_TEXT[direction]
            )}
          >
            <TrendArrow direction={direction} className="size-[0.5625rem]" />
            {formatRupees(summary.pnl, { decimals: 0, signed: true })}
          </dd>
          <dd
            className={cn(
              'text-[0.71875rem] font-semibold',
              TREND_TEXT[directionOf(summary.percent)]
            )}
          >
            {formatPercent(summary.percent)}
          </dd>
        </div>
      </dl>
    </section>
  )
}
