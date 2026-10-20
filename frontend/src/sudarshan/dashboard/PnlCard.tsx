import { Wallet } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { directionOf, formatRupees, toNumber } from './format'
import { TREND_TEXT, TrendArrow } from './Trend'

/** The funds summary /auth/dashboard-data returns, figures as strings. */
export interface DashboardFunds {
  availablecash: string
  collateral: string
  m2munrealized: string
  m2mrealized: string
  utiliseddebits: string
}

/**
 * How much of the margin is in use, from the fields the funds call returns.
 *
 * With Upstox (the V3 funds mapping in broker/upstox/api/funds.py) the cash
 * available and the pledge available are both what is left after margin used,
 * and margin used counts cash and pledge alike, so the three together are all
 * the margin there is. A negative available (a shortfall) adds nothing to it,
 * which reads as 100% used. Null when there is nothing to measure against,
 * as outside the broker's funds hours, when every figure comes back zero.
 */
export function marginUse(
  funds: DashboardFunds
): { used: number; total: number; percent: number } | null {
  const used = Math.max(toNumber(funds.utiliseddebits) ?? 0, 0)
  const available = Math.max(toNumber(funds.availablecash) ?? 0, 0)
  const pledge = Math.max(toNumber(funds.collateral) ?? 0, 0)
  const total = used + available + pledge
  if (total <= 0) return null
  return { used, total, percent: (used / total) * 100 }
}

function percentLabel(percent: number): string {
  if (percent <= 0) return '0%'
  if (percent < 1) return '<1%'
  if (percent >= 100) return '100%'
  // 99.6% of the margin is not all of it: never round up to a full bar's label.
  return `${Math.min(99, Math.round(percent))}%`
}

interface PnlCardProps {
  funds: DashboardFunds | null
  loading: boolean
}

/**
 * Today's P&L (realised plus unrealised, as the broker reports them for the
 * day's positions), the funds available and the share of margin in use. Every
 * figure is the broker's, refreshed by the dashboard after each order event;
 * nothing here is estimated.
 */
export function PnlCard({ funds, loading }: PnlCardProps) {
  const realised = toNumber(funds?.m2mrealized) ?? 0
  const unrealised = toNumber(funds?.m2munrealized) ?? 0
  const pnl = realised + unrealised
  const available = toNumber(funds?.availablecash) ?? 0
  const pledge = toNumber(funds?.collateral) ?? 0
  const margin = funds ? marginUse(funds) : null
  const showFigures = !loading && funds !== null
  const direction = directionOf(pnl)

  return (
    <section
      aria-labelledby="sd-pnl-title"
      aria-busy={loading || undefined}
      className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 id="sd-pnl-title" className="text-[0.84375rem] font-semibold text-muted-foreground">
        Today&apos;s P&amp;L
      </h2>

      {showFigures ? (
        <p className={cn('flex items-center gap-2', TREND_TEXT[direction])} data-testid="pnl-total">
          <TrendArrow direction={direction} className="size-[0.9375rem]" />
          <span className="sd-display text-[2.625rem] leading-[1.05] font-bold">
            {formatRupees(pnl, { signed: true })}
          </span>
        </p>
      ) : (
        <Skeleton className="my-1 h-10 w-52" />
      )}

      {showFigures && (
        <p className="flex flex-wrap gap-x-3.5 gap-y-0.5 text-[0.8125rem] text-muted-foreground">
          <span>
            Realised{' '}
            <strong className={cn('font-bold', TREND_TEXT[directionOf(realised)])}>
              {formatRupees(realised, { signed: true })}
            </strong>
          </span>
          <span>
            Unrealised{' '}
            <strong className={cn('font-bold', TREND_TEXT[directionOf(unrealised)])}>
              {formatRupees(unrealised, { signed: true })}
            </strong>
          </span>
        </p>
      )}

      <div aria-hidden="true" className="my-2 h-px bg-[color:var(--divider)]" />

      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <Wallet aria-hidden="true" className="size-[1.125rem]" />
          Funds available
        </span>
        {showFigures ? (
          <span
            className={cn(
              'sd-display text-[1.375rem] leading-[1.1] font-semibold',
              available < 0 && TREND_TEXT.down
            )}
          >
            {formatRupees(available)}
          </span>
        ) : (
          <Skeleton className="h-6 w-28" />
        )}
      </div>

      {showFigures && pledge > 0 && (
        <div className="flex items-center justify-between gap-3 text-[0.8125rem] font-medium text-muted-foreground">
          <span>Collateral</span>
          <span className="font-semibold text-foreground">{formatRupees(pledge)}</span>
        </div>
      )}

      {showFigures && margin && (
        <div className="mt-0.5 flex flex-col gap-1">
          <p className="flex justify-between gap-3 text-[0.78125rem] font-medium text-muted-foreground">
            <span>
              Margin used {formatRupees(margin.used, { decimals: 0 })} of{' '}
              {formatRupees(margin.total, { decimals: 0 })}
            </span>
            <span className="font-semibold text-foreground">{percentLabel(margin.percent)}</span>
          </p>
          {/* The words above carry the figure; the bar only shows it. */}
          <div
            aria-hidden="true"
            data-testid="margin-bar"
            className="h-1.5 overflow-hidden rounded-full bg-[color:var(--surface-3)]"
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{
                width: `${margin.percent > 0 ? Math.max(Math.min(margin.percent, 100), 1.5) : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {showFigures && !margin && (
        <p className="text-[0.78125rem] font-medium text-muted-foreground">No margin in use</p>
      )}
    </section>
  )
}
