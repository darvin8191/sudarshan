import { CandlestickChart } from 'lucide-react'
import { memo } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { chartPath } from '@/sudarshan/chart/prefs'
import { formatRupees } from '@/sudarshan/dashboard/format'
import { describe } from '@/sudarshan/watchlist/rows'
import type { Trade } from '@/types/trading'
import { clockOf, figure, momentOf, units } from './format'
import { CARD, price, SideChip } from './parts'

/**
 * The trade book's cards, one per order (B3: M6.2): its side and name, how many fills it took,
 * their quantity, the average price weighted by quantity and their value, and each fill under
 * it with its own quantity, price and time. An order that filled at once reads as one fill
 * always did. A fill with no order id stands alone. A tap opens the chart.
 */

export interface TradeGroup {
  key: string
  symbol: string
  exchange: string
  action: string
  product: string
  /** The order's fills, oldest first. */
  fills: Trade[]
  /** Their total, or null when one of them gives no quantity. */
  quantity: number | null
  /** Weighted by quantity; null when one of them gives no quantity or price. */
  average: number | null
  /** Their total value, or null when one of them gives none. */
  value: number | null
  /** When the last of them came. */
  at: number
}

function groupOf(key: string, list: Trade[]): TradeGroup {
  const fills = [...list].sort((a, b) => momentOf(a.timestamp) - momentOf(b.timestamp))
  let quantity: number | null = 0
  let cost: number | null = 0
  let value: number | null = 0
  for (const fill of fills) {
    const filled = figure(fill.quantity)
    const at = figure(fill.average_price)
    const worth = figure(fill.trade_value)
    quantity = quantity === null || filled === null ? null : quantity + filled
    cost = cost === null || filled === null || at === null ? null : cost + filled * at
    value = value === null || worth === null ? null : value + worth
  }
  const first = fills[0]
  return {
    key,
    symbol: first.symbol,
    exchange: first.exchange,
    action: first.action,
    product: first.product,
    fills,
    quantity,
    average: quantity && cost !== null ? cost / quantity : null,
    value,
    at: momentOf(fills[fills.length - 1].timestamp),
  }
}

/** The fills by order, the order whose last fill came latest first. */
export function groupTrades(trades: readonly Trade[]): TradeGroup[] {
  const byOrder = new Map<string, Trade[]>()
  trades.forEach((trade, index) => {
    const orderid = String(trade.orderid ?? '').trim()
    const key = orderid ? `order:${orderid}` : `fill:${index}`
    byOrder.set(key, [...(byOrder.get(key) ?? []), trade])
  })
  return [...byOrder].map(([key, list]) => groupOf(key, list)).sort((a, b) => b.at - a.at)
}

export const TradeGroups = memo(function TradeGroups({ groups }: { groups: TradeGroup[] }) {
  return (
    <ul aria-label="Trades" className="flex flex-col gap-2">
      {groups.map((group) => (
        <TradeGroupCard key={group.key} group={group} />
      ))}
    </ul>
  )
})

/** One order's fills; a tap opens its chart. */
const TradeGroupCard = memo(function TradeGroupCard({ group }: { group: TradeGroup }) {
  const { symbol, exchange, fills } = group
  const named = describe(symbol, exchange)
  const several = fills.length > 1
  return (
    <li data-trade-group={group.key}>
      <Link to={chartPath(symbol, exchange)} className={cn(CARD, 'no-underline')}>
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-2">
            <SideChip side={group.action} />
            <span
              className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
              title={symbol}
            >
              {named.title}
            </span>
          </span>
          <span className="shrink-0 text-xs font-medium text-[color:var(--text-tertiary)]">
            {clockOf(fills[fills.length - 1].timestamp)}
          </span>
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            {named.detail} · {group.product}
          </span>
          <span className="shrink-0 text-[0.8125rem] font-bold whitespace-nowrap">
            {group.quantity === null ? '-' : units(group.quantity)}
            <span className="font-medium text-[color:var(--text-secondary)]">
              {several ? ' at avg ' : ' at '}
            </span>
            {price(group.average, exchange)}
          </span>
        </span>
        {several && (
          <span
            data-testid="fills"
            className="flex flex-col gap-0.5 border-t border-[color:var(--divider)] pt-1.5 text-xs font-medium text-[color:var(--text-secondary)]"
          >
            {fills.map((fill, index) => {
              const filled = figure(fill.quantity)
              return (
                <span key={`${fill.timestamp}-${index}`} className="flex justify-between gap-3">
                  <span>
                    {filled === null ? '-' : units(filled)} at{' '}
                    {price(figure(fill.average_price), exchange)}
                  </span>
                  <span className="text-[color:var(--text-tertiary)]">
                    {clockOf(fill.timestamp)}
                  </span>
                </span>
              )
            })}
          </span>
        )}
        <span className="flex items-center justify-between gap-3 text-xs font-medium text-[color:var(--text-tertiary)]">
          <span className="inline-flex items-center gap-1">
            <CandlestickChart aria-hidden="true" className="size-3.5" />
            Chart
          </span>
          <span>
            {several && `${fills.length} fills`}
            {several && group.value !== null && ' · '}
            {group.value !== null && `Value ${formatRupees(group.value)}`}
          </span>
        </span>
      </Link>
    </li>
  )
})
