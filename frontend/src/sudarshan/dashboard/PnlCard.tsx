import { AlertTriangle, Wallet } from 'lucide-react'
import { useMemo } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { useMarketData } from '@/hooks/useMarketData'
import { cn } from '@/lib/utils'
import { directionOf, formatRupees, toNumber } from './format'
import { useNow } from './hooks'
import { LiveLine } from './LiveLine'
import {
  anyOpen,
  feedLine,
  latest,
  liveMove,
  type MarketOpen,
  openPositions,
  type PositionsSnapshot,
} from './live'
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
  /** The position book, read just before these funds; null when it could not be read. */
  positions?: PositionsSnapshot | null
  /** When the funds and the position book were last read. */
  syncedAt?: number | null
  /** The last re-read failed, so the figures shown are from the one before it. */
  syncFailed?: boolean
  marketOpen?: MarketOpen
}

const NOT_KNOWN: MarketOpen = () => null

function openCount(count: number): string {
  if (count === 0) return 'No open positions'
  return count === 1 ? '1 open position' : `${count} open positions`
}

/**
 * Today's P&L, live, then the funds available and the share of margin in use.
 *
 * The broker's own figures are the base: realised and unrealised as the funds
 * call reports them, re-read together with the position book after every order
 * event and every 45 seconds while a market is open. Between re-reads the
 * unrealised part moves on every tick of an open position, by what that
 * position has made or lost since the book was read (`liveMove`, priced by the
 * Positions page's own evaluator). Realised P&L moves only with a trade, and a
 * trade re-reads the broker. Funds and margin are as of the last re-read.
 */
export function PnlCard({
  funds,
  loading,
  positions = null,
  syncedAt = null,
  syncFailed = false,
  marketOpen = NOT_KNOWN,
}: PnlCardProps) {
  const held = useMemo(() => openPositions(positions?.positions ?? []), [positions])
  const symbols = useMemo(() => held.map(({ symbol, exchange }) => ({ symbol, exchange })), [held])
  const {
    data: prices,
    isAuthenticated,
    isPaused,
  } = useMarketData({ symbols, mode: 'LTP', enabled: symbols.length > 0 })
  const now = useNow()
  const move = liveMove(positions, prices)

  const realised = toNumber(funds?.m2mrealized) ?? 0
  const unrealised = (toNumber(funds?.m2munrealized) ?? 0) + move.delta
  const pnl = realised + unrealised
  const available = toNumber(funds?.availablecash) ?? 0
  const pledge = toNumber(funds?.collateral) ?? 0
  const margin = funds ? marginUse(funds) : null
  const showFigures = !loading && funds !== null
  const direction = directionOf(pnl)
  const line = feedLine({
    now,
    marketOpen:
      held.length > 0
        ? anyOpen(held.map((position) => marketOpen(position.exchange)))
        : marketOpen('NSE'),
    lastTickAt: move.lastTickAt,
    lastUpdateAt: latest(move.lastTickAt, syncedAt),
    ticksDue: held.length > 0,
    connected: isAuthenticated && !isPaused,
  })
  // Figures that ticks should be moving and are not: greyed, keeping sign and
  // arrow, so an old number never looks current. Realised P&L does not move
  // with prices, so it keeps its colour.
  const stale = (line.kind === 'delayed' || line.kind === 'connecting') && line.stale

  return (
    <section
      aria-labelledby="sd-pnl-title"
      aria-busy={loading || undefined}
      className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex min-h-5 items-center justify-between gap-3">
        <h2 id="sd-pnl-title" className="text-[0.84375rem] font-semibold text-muted-foreground">
          Today&apos;s P&amp;L
        </h2>
        {showFigures && <LiveLine line={line} now={now} />}
      </div>

      {showFigures ? (
        <p
          className={cn(
            'flex items-center gap-2',
            stale ? 'text-muted-foreground' : TREND_TEXT[direction]
          )}
          data-testid="pnl-total"
        >
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
            <strong
              className={cn(
                'font-bold',
                stale ? 'text-muted-foreground' : TREND_TEXT[directionOf(unrealised)]
              )}
              data-testid="pnl-unrealised"
            >
              {formatRupees(unrealised, { signed: true })}
            </strong>
          </span>
        </p>
      )}

      {showFigures && positions && (
        <p className="text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
          {openCount(held.length)}
        </p>
      )}

      {showFigures && syncFailed && (
        <output className="flex items-start gap-1.5 text-[0.78125rem] font-medium">
          <AlertTriangle
            aria-hidden="true"
            className="mt-px size-3.5 shrink-0 text-[color:var(--warning-text)]"
          />
          Could not refresh just now. Showing the last figures.
        </output>
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
