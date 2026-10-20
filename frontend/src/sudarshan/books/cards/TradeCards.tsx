import { CandlestickChart } from 'lucide-react'
import { memo, type ReactNode, useCallback, useMemo } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { chartPath } from '@/sudarshan/chart/prefs'
import { formatRupees } from '@/sudarshan/dashboard/format'
import { describe } from '@/sudarshan/watchlist/rows'
import type { Trade } from '@/types/trading'
import { clockOf, figure, momentOf, units } from './format'
import {
  BookHeading,
  CARD,
  EmptyCard,
  LoadingCards,
  ProblemCard,
  price,
  SideChip,
  StaleBookNote,
} from './parts'
import { useLatest } from './useLatest'

/**
 * The trade book on a phone (M6.9): one card per fill, newest first, with its
 * side, quantity, price and time. A trade is done, so there is nothing to do on
 * it but look: a tap opens its chart.
 */

export interface TradeCardsProps {
  trades: Trade[]
  loading: boolean
  error: string | null
  refreshing: boolean
  onRefresh: (showRefresh?: boolean) => unknown
}

export function TradeCards(props: TradeCardsProps) {
  const onRefresh = useLatest(props.onRefresh)
  return <TradeBoard {...props} onRefresh={onRefresh} />
}

const TradeBoard = memo(function TradeBoard({
  trades,
  loading,
  error,
  refreshing,
  onRefresh,
}: TradeCardsProps) {
  const refresh = useCallback(() => void onRefresh(true), [onRefresh])
  const sorted = useMemo(
    () => [...trades].sort((a, b) => momentOf(b.timestamp) - momentOf(a.timestamp)),
    [trades]
  )
  const buys = trades.filter((trade) => String(trade.action).toUpperCase() === 'BUY').length

  let content: ReactNode
  if (trades.length === 0 && loading) {
    content = <LoadingCards />
  } else if (trades.length === 0 && error) {
    content = <ProblemCard message="The trade book could not be read just now." onRetry={refresh} />
  } else if (trades.length === 0) {
    content = <EmptyCard title="No trades today" hint="Every fill shows here as it happens." />
  } else {
    content = (
      <>
        <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {trades.length} {trades.length === 1 ? 'trade' : 'trades'} · {buys} buy ·{' '}
          {trades.length - buys} sell
        </p>
        {error && <StaleBookNote text="Could not refresh just now. Showing the last trades." />}
        <ul aria-label="Trades" className="flex flex-col gap-2">
          {sorted.map((trade, index) => (
            <TradeCard
              key={`${trade.orderid}-${trade.timestamp}-${index}`}
              symbol={trade.symbol}
              exchange={trade.exchange}
              action={trade.action}
              product={trade.product}
              quantity={figure(trade.quantity)}
              average={figure(trade.average_price)}
              value={figure(trade.trade_value)}
              time={clockOf(trade.timestamp)}
            />
          ))}
        </ul>
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <BookHeading title="Trades" refreshing={refreshing} onRefresh={refresh} />
      {content}
    </div>
  )
})

/** One fill; a tap opens its chart. */
const TradeCard = memo(function TradeCard({
  symbol,
  exchange,
  action,
  product,
  quantity,
  average,
  value,
  time,
}: {
  symbol: string
  exchange: string
  action: string
  product: string
  quantity: number | null
  average: number | null
  value: number | null
  time: string
}) {
  const named = describe(symbol, exchange)
  return (
    <li>
      <Link to={chartPath(symbol, exchange)} className={cn(CARD, 'no-underline')}>
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-2">
            <SideChip side={action} />
            <span
              className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
              title={symbol}
            >
              {named.title}
            </span>
          </span>
          <span className="shrink-0 text-xs font-medium text-[color:var(--text-tertiary)]">
            {time}
          </span>
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            {named.detail} · {product}
          </span>
          <span className="shrink-0 text-[0.8125rem] font-bold whitespace-nowrap">
            {quantity === null ? '-' : units(quantity)}
            <span className="font-medium text-[color:var(--text-secondary)]"> at </span>
            {price(average, exchange)}
          </span>
        </span>
        <span className="flex items-center justify-between gap-3 text-xs font-medium text-[color:var(--text-tertiary)]">
          <span className="inline-flex items-center gap-1">
            <CandlestickChart aria-hidden="true" className="size-3.5" />
            Chart
          </span>
          {value !== null && <span>Value {formatRupees(value)}</span>}
        </span>
      </Link>
    </li>
  )
})
