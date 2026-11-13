import { ArrowDown, ArrowUp, Clock, ListPlus, Plus, Trash2 } from 'lucide-react'
import { memo } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatPrice } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { ChipMark } from '@/sudarshan/screener/ChipMark'
import type { Chip } from '@/sudarshan/screener/types'
import { type ArrangeEdit, ArrangeRows } from './ArrangeSheet'
import { describe, formatChange, priceDecimals, type TickDirection } from './rows'
import { SectionHeader } from './SectionHeader'
import type { LiveRow } from './useLiveRows'
import type { Group } from './v2/sections'
import type { Section } from './v2/types'

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

function Name({
  symbol,
  exchange,
  chip,
}: {
  symbol: string
  exchange: string
  /** SUDARSHAN M9 UI7: what a screener says about this row (New, Held, Pinned), an icon and a word. */
  chip?: Chip
}) {
  const named = describe(symbol, exchange)
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate text-[0.9375rem] leading-tight font-semibold" title={symbol}>
          {named.title}
        </span>
        {chip && <ChipMark chip={chip} />}
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
 * and the arrow kept, a clock beside them. Prices take the exchange's decimals
 * (`priceDecimals`: four on the currency segments).
 */
export function Figures({
  ltp,
  change,
  changePercent,
  direction,
  stale,
  size = 'row',
  decimals = 2,
}: FigureValues & { size?: 'row' | 'sheet'; decimals?: number }) {
  const day = change === null ? 'flat' : directionOf(change, decimals)
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
          formatPrice(ltp, decimals)
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
          <span>{`${formatChange(change, decimals)} (${formatPercent(changePercent)})`}</span>
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
  chip,
  onOpen,
  ...figures
}: FigureValues & {
  itemId: number
  symbol: string
  exchange: string
  chip?: Chip
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
        <Name symbol={symbol} exchange={exchange} chip={chip} />
        <Figures {...figures} decimals={priceDecimals(exchange)} />
      </button>
    </li>
  )
})

/** F3 (v2): the open own list in sections: the view's groups, what is folded, and the taps. */
export interface SectionLayout {
  groups: Group[]
  collapsed: ReadonlySet<number>
  onToggle: (id: number) => void
  onMenu: (id: number) => void
}

/** F3: no section first, then each section under its header; a folded one shows only its header. */
function Sectioned({
  rows,
  layout,
  tags,
  onOpen,
}: {
  rows: LiveRow[]
  layout: SectionLayout
  tags?: ReadonlyMap<string, Chip>
  onOpen: (itemId: number) => void
}) {
  const byId = new Map(rows.map((row) => [row.item.id, row]))
  const shown = new Set(layout.groups.flatMap((group) => group.ids))
  // An item the view does not list yet (the two reads crossed) is shown with the unsectioned.
  const extra = rows.filter((row) => !shown.has(row.item.id)).map((row) => row.item.id)
  return (
    <ul className="divide-y divide-[color:var(--divider)]">
      {layout.groups.flatMap((group) => {
        const ids = group.section ? group.ids : [...group.ids, ...extra]
        const section: Section | null = group.section
        const open = ids.flatMap((id) => {
          const row = byId.get(id)
          return row && !layout.collapsed.has(section?.id ?? -1)
            ? [
                <PriceRow
                  key={row.item.id}
                  itemId={row.item.id}
                  symbol={row.item.symbol}
                  exchange={row.item.exchange}
                  chip={tags?.get(row.key)}
                  onOpen={onOpen}
                  {...figuresOf(row)}
                />,
              ]
            : []
        })
        return section
          ? [
              <SectionHeader
                key={`section-${section.id}`}
                section={section}
                count={ids.length}
                collapsed={layout.collapsed.has(section.id)}
                onToggle={layout.onToggle}
                onMenu={layout.onMenu}
              />,
              ...open,
            ]
          : open
      })}
    </ul>
  )
}

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
  tags,
  sections,
  arrange,
}: {
  rows: LiveRow[]
  loading: boolean
  editing: boolean
  onOpen: (itemId: number) => void
  onMove: (item: WatchlistItem, by: -1 | 1) => void
  onRemove: (item: WatchlistItem) => void
  /** Absent while there is no API key to search with: the page says why. */
  onAdd?: () => void
  /** The chip of each row a screener fed, by EXCHANGE:SYMBOL (M9 UI7). */
  tags?: ReadonlyMap<string, Chip>
  /** v2 (F3): the rows in sections. Absent with the switch off, and while editing (flat). */
  sections?: SectionLayout
  /** v2 (F5): edit mode as a draft with sections. Absent with the switch off. */
  arrange?: ArrangeEdit
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

  // F5: editing an own list in v2 arranges a draft.
  if (editing && arrange)
    return <ArrangeRows edit={arrange} items={rows.map((row) => row.item)} onRemove={onRemove} />

  const sectioned = sections && !editing && sections.groups.length > 1
  if (rows.length === 0 && !sectioned) {
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

  if (sections && sectioned)
    return <Sectioned rows={rows} layout={sections} tags={tags} onOpen={onOpen} />

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
              chip={tags?.get(row.key)}
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
