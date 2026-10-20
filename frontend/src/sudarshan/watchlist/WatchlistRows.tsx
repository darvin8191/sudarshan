import { ArrowDown, ArrowUp, Clock, ListPlus, Plus, Trash2 } from 'lucide-react'
import { memo } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatPrice } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { describe, formatChange, type TickDirection } from './rows'
import type { LiveRow } from './useLiveRows'

/** Grey for a level that has stopped arriving; its sign and arrow stay. */
const STALE = 'text-[color:var(--stale)]'

/** A 48dp square for an icon button. */
const ICON_BUTTON =
  'flex size-12 shrink-0 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent active:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35'

/** What a row's figures are drawn from, as plain values, so a row that has not moved is not redrawn. */
export interface FigureValues {
  ltp: number | null
  change: number | null
  changePercent: number | null
  direction: TickDirection | null
  stale: boolean
}

export function figuresOf(row: LiveRow): FigureValues {
  return {
    ltp: row.view.ltp,
    change: row.view.change,
    changePercent: row.view.changePercent,
    direction: row.direction,
    stale: row.stale,
  }
}

function Name({ symbol, exchange }: { symbol: string; exchange: string }) {
  const named = describe(symbol, exchange)
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className="truncate text-[0.9375rem] leading-tight font-semibold" title={symbol}>
        {named.title}
      </span>
      <span className="truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
        {named.detail}
      </span>
    </span>
  )
}

/**
 * The level and its change. The level carries the last tick's direction, in
 * the up or down colour with a solid arrow; the line under it is the change on
 * the previous close, signed. No flash on a tick. Stale: both grey, the sign
 * and the arrow kept, a clock beside them.
 */
export function Figures({
  ltp,
  change,
  changePercent,
  direction,
  stale,
  size = 'row',
}: FigureValues & { size?: 'row' | 'sheet' }) {
  const day = change === null ? 'flat' : directionOf(change)
  return (
    <span className="flex shrink-0 flex-col items-end gap-0.5 text-right">
      <span
        data-tick={direction ?? undefined}
        className={cn(
          'flex items-center gap-1 leading-tight font-bold',
          size === 'row' ? 'text-base' : 'sd-display text-[1.75rem]',
          stale ? STALE : TREND_TEXT[direction ?? 'flat']
        )}
      >
        {direction && (
          <TrendArrow direction={direction} className={size === 'row' ? 'size-2.5' : 'size-3.5'} />
        )}
        {ltp !== null ? (
          formatPrice(ltp)
        ) : (
          <>
            <span aria-hidden="true">&mdash;</span>
            <span className="sr-only">No price yet</span>
          </>
        )}
      </span>
      <span
        className={cn(
          'flex min-h-4 items-center gap-1 text-xs leading-tight font-semibold',
          size === 'sheet' && 'text-sm',
          stale ? STALE : TREND_TEXT[day]
        )}
      >
        {change !== null && changePercent !== null && (
          <span>{`${formatChange(change)} (${formatPercent(changePercent)})`}</span>
        )}
        {stale && (
          <>
            <Clock aria-hidden="true" className="size-3" />
            <span className="sr-only">Stale</span>
          </>
        )}
      </span>
    </span>
  )
}

/** One row of the open list: redrawn only when its own figures change. */
const PriceRow = memo(function PriceRow({
  itemId,
  symbol,
  exchange,
  onOpen,
  ...figures
}: FigureValues & {
  itemId: number
  symbol: string
  exchange: string
  onOpen: (itemId: number) => void
}) {
  return (
    <li data-stale={figures.stale || undefined}>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onOpen(itemId)}
        className="flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <Name symbol={symbol} exchange={exchange} />
        <Figures {...figures} />
      </button>
    </li>
  )
})

/**
 * The open list's rows: 56dp or taller, the name and venue on the left, the
 * level and the day's change on the right. A tap opens the instrument's sheet.
 * In edit mode each row moves up or down a place, or comes out of the list,
 * with 48dp buttons instead.
 */
export function WatchlistRows({
  rows,
  loading,
  editing,
  onOpen,
  onMove,
  onRemove,
  onAdd,
}: {
  rows: LiveRow[]
  loading: boolean
  editing: boolean
  onOpen: (itemId: number) => void
  onMove: (item: WatchlistItem, by: -1 | 1) => void
  onRemove: (item: WatchlistItem) => void
  /** Absent while there is no API key to search with: the page says why. */
  onAdd?: () => void
}) {
  if (loading) {
    return (
      <ul aria-label="Loading" className="divide-y divide-[color:var(--divider)]">
        {[0, 1, 2, 3].map((index) => (
          <li key={index} className="flex min-h-14 items-center justify-between gap-3 px-4">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-20" />
          </li>
        ))}
      </ul>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
        <span className="flex size-12 items-center justify-center rounded-2xl bg-[color:var(--primary-tint)] text-primary">
          <ListPlus aria-hidden="true" className="size-6" />
        </span>
        <h2 className="text-base font-bold">This list is empty</h2>
        <p className="max-w-xs text-sm text-muted-foreground">
          Add the symbols you follow. Their prices update tick by tick.
        </p>
        {onAdd && (
          <button
            type="button"
            onClick={onAdd}
            className="inline-flex min-h-12 items-center gap-2 rounded-[0.875rem] bg-primary px-5 text-[0.9375rem] font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <Plus aria-hidden="true" className="size-5" />
            Add symbols
          </button>
        )}
      </div>
    )
  }

  return (
    <ul className="divide-y divide-[color:var(--divider)]">
      {rows.map((row, index) => {
        const { item } = row
        if (!editing) {
          return (
            <PriceRow
              key={item.id}
              itemId={item.id}
              symbol={item.symbol}
              exchange={item.exchange}
              onOpen={onOpen}
              {...figuresOf(row)}
            />
          )
        }
        const title = describe(item.symbol, item.exchange).title
        return (
          <li key={item.id} className="flex min-h-14 items-center gap-1 py-1 pr-1 pl-4">
            <Name symbol={item.symbol} exchange={item.exchange} />
            <button
              type="button"
              className={ICON_BUTTON}
              disabled={index === 0}
              onClick={() => onMove(item, -1)}
              aria-label={`Move ${title} up`}
            >
              <ArrowUp aria-hidden="true" className="size-5" />
            </button>
            <button
              type="button"
              className={ICON_BUTTON}
              disabled={index === rows.length - 1}
              onClick={() => onMove(item, 1)}
              aria-label={`Move ${title} down`}
            >
              <ArrowDown aria-hidden="true" className="size-5" />
            </button>
            <button
              type="button"
              className={cn(ICON_BUTTON, 'text-[color:var(--down)]')}
              onClick={() => onRemove(item)}
              aria-label={`Remove ${title}`}
            >
              <Trash2 aria-hidden="true" className="size-5" />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
