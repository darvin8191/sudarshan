/**
 * The book on the phone chart (M4.15, C9): a strip with the best bid and ask
 * and their quantities, moving tick by tick, and, on a tap, the five levels in
 * a sheet. Both read the chart's own depth stream, the one subscription it
 * already holds; nothing else is asked for. Bid and ask are named in words and
 * placed apart; their colours only repeat what the words say.
 *
 * While prices are not real-time (reconnecting, delayed, or the market shut)
 * the book is drawn grey with the reason, never as current.
 */
import { ChevronRight } from 'lucide-react'
import type { DepthLevel, MarketDepth } from 'openalgo-charts'
import { useSyncExternalStore } from 'react'
import { cn } from '@/lib/utils'
import { formatPrice } from '@/sudarshan/dashboard/format'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { PriceStore } from './prices'

const quantity = new Intl.NumberFormat('en-IN')

/** A level that holds an order: the proxy pads an empty one with zeros. */
const real = (level: DepthLevel | undefined): level is DepthLevel =>
  !!level && level.price > 0 && level.qty > 0

export function DepthStrip({
  prices,
  decimals,
  quiet,
  onOpen,
}: {
  prices: PriceStore
  decimals: number
  /** Why the book is not current, or null while it is. */
  quiet: string | null
  onOpen(): void
}) {
  const { depth } = useSyncExternalStore(prices.subscribe, prices.get)
  const bid = depth?.bids[0]
  const ask = depth?.asks[0]
  const side = (label: string, level: DepthLevel | undefined, align: string) => (
    <span className={cn('flex min-w-0 flex-col leading-tight', align)}>
      <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
        {label}
      </span>
      <span className="truncate text-sm font-bold tabular-nums">
        {real(level) ? (
          <>
            {formatPrice(level.price, decimals)}
            <span className="font-medium text-[color:var(--text-secondary)]">
              {' '}
              × {quantity.format(level.qty)}
            </span>
          </>
        ) : (
          '—'
        )}
      </span>
    </span>
  )
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Market depth: bid ${real(bid) ? formatPrice(bid.price, decimals) : 'none'}, ask ${real(ask) ? formatPrice(ask.price, decimals) : 'none'}${quiet ? `, ${quiet}` : ''}. Show five levels`}
      data-testid="depth-strip"
      className={cn(
        'flex h-12 w-full shrink-0 items-center gap-3 border-b bg-card px-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        quiet && 'text-muted-foreground'
      )}
    >
      {side('Bid', bid, 'flex-1 items-start')}
      {side('Ask', ask, 'flex-1 items-end text-right')}
      <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
    </button>
  )
}

export function DepthSheet({
  open,
  onOpenChange,
  prices,
  decimals,
  quiet,
}: {
  open: boolean
  onOpenChange(open: boolean): void
  prices: PriceStore
  decimals: number
  quiet: string | null
}) {
  const { depth } = useSyncExternalStore(prices.subscribe, prices.get)
  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Market depth"
      description="Five levels a side"
    >
      {quiet && (
        <p className="pb-2 text-sm font-medium text-[color:var(--warning-text)]">{quiet}</p>
      )}
      {depth ? (
        <Levels depth={depth} decimals={decimals} quiet={quiet !== null} />
      ) : (
        <p className="py-2 text-sm text-muted-foreground">The book shows with the next tick.</p>
      )}
    </BottomSheet>
  )
}

function Levels({
  depth,
  decimals,
  quiet,
}: {
  depth: MarketDepth
  decimals: number
  quiet: boolean
}) {
  const cell = (level: DepthLevel | undefined, field: 'price' | 'quantity') =>
    !real(level)
      ? '—'
      : field === 'price'
        ? formatPrice(level.price, decimals)
        : quantity.format(level.qty)
  return (
    <table
      className={cn(
        'w-full table-fixed border-collapse text-right text-sm tabular-nums',
        quiet && 'text-muted-foreground'
      )}
    >
      <thead>
        <tr className="text-xs font-semibold text-[color:var(--text-tertiary)]">
          <th scope="col" className="px-2 pb-1 font-semibold">
            Bid qty
          </th>
          <th scope="col" className="px-2 pb-1 font-semibold text-[color:var(--buy-solid)]">
            Bid
          </th>
          <th scope="col" className="px-2 pb-1 font-semibold text-[color:var(--sell-solid)]">
            Ask
          </th>
          <th scope="col" className="px-2 pb-1 font-semibold">
            Ask qty
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-[color:var(--divider)]">
        {[0, 1, 2, 3, 4].map((i) => (
          <tr key={i} className="h-10">
            <td className="px-2">{cell(depth.bids[i], 'quantity')}</td>
            <td className="px-2 font-semibold">{cell(depth.bids[i], 'price')}</td>
            <td className="px-2 font-semibold">{cell(depth.asks[i], 'price')}</td>
            <td className="px-2">{cell(depth.asks[i], 'quantity')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
