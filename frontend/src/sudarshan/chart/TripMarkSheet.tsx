import { ArrowDown, ArrowUp } from 'lucide-react'
import { istTime } from '@/sudarshan/books/time'
import { formatPrice } from '@/sudarshan/dashboard/format'
import { PRICE_SCALE } from '@/sudarshan/journal/types'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { MarkLeg } from './tripMarkers'

/**
 * SUDARSHAN M14.8 (review L4): what a trade's mark stands for, opened by a tap
 * on it. A mark on narrow candles carries its letter only, so each of its
 * fills is written here in full: the time in India, bought or sold, how many
 * and at what price. Read-only: nothing here can send an order.
 */
export function TripMarkSheet({
  fills,
  decimals,
  onOpenChange,
}: {
  /** The fills of the mark tapped; null keeps the sheet shut. */
  fills: readonly MarkLeg[] | null
  decimals: number
  onOpenChange(open: boolean): void
}) {
  const first = fills?.[0]
  const title = first
    ? `${first.role === 'entry' ? 'Entry' : 'Exit'}: ${first.action === 'BUY' ? 'bought' : 'sold'} at this candle`
    : 'Fills at this candle'
  return (
    <BottomSheet
      open={!!fills?.length}
      onOpenChange={onOpenChange}
      title={title}
      description="From your journal"
    >
      <ul data-testid="trip-mark-fills" className="flex flex-col">
        {(fills ?? []).map((fill) => {
          const buy = fill.action === 'BUY'
          const Icon = buy ? ArrowUp : ArrowDown
          return (
            <li
              key={`${fill.exch_ts_ms}:${fill.qty}:${fill.price_u}`}
              className="flex min-h-12 items-center gap-3 border-t border-[color:var(--divider)] text-[0.9375rem] first:border-t-0"
            >
              <span className="w-[4.5rem] shrink-0 text-[color:var(--text-secondary)]">
                {istTime(fill.exch_ts_ms)}
              </span>
              <span
                className="inline-flex items-center gap-1 font-semibold"
                style={{ color: `var(${buy ? '--buy-solid' : '--sell-solid'})` }}
              >
                <Icon aria-hidden="true" className="size-3.5" strokeWidth={3} />
                {buy ? 'Bought' : 'Sold'}
              </span>
              <span className="ml-auto text-right">
                {fill.qty} at {formatPrice(fill.price_u / PRICE_SCALE, decimals)}
              </span>
            </li>
          )
        })}
      </ul>
    </BottomSheet>
  )
}
