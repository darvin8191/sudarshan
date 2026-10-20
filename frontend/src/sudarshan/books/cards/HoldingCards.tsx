import { CandlestickChart, Minus, Plus } from 'lucide-react'
import { memo, type ReactNode, useCallback, useEffect } from 'react'
import { calculateLiveStats } from '@/hooks/useLivePrice'
import { cn } from '@/lib/utils'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import type { TickDirection } from '@/sudarshan/books/prices'
import { SymbolChartLink } from '@/sudarshan/chart/SymbolChartLink'
import { formatRupees } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { FeedLine, MarketOpen } from '@/sudarshan/dashboard/live'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import type { Holding, HoldingsStats } from '@/types/trading'
import { figure, units } from './format'
import {
  ACTION,
  ActionWords,
  BookHeading,
  BUY_FILL,
  CARD,
  Details,
  EmptyCard,
  Last,
  LoadingCards,
  Money,
  OUTLINED,
  ProblemCard,
  price,
  SELL_FILL,
  STALE,
  SignedPercent,
  StaleBookNote,
  useSandbox,
} from './parts'
import { useBookOverlay, usePageDialog } from './useBookOverlay'
import { type PricedRow, useFramePrices } from './useFramePrices'
import { useLatest } from './useLatest'

/**
 * Holdings on a phone (M6.9): what they are worth now, what went into them, the
 * overall P&L and the day's change at the top, one card per holding, and a sheet
 * per holding. Buy more and Sell open the page's own order ticket as its
 * table's Add and Exit do (the same holding, delivery). Sell starts from the
 * whole holding, as Exit does; Buy more starts from one share, so a market
 * buy of the entire holding again is never one tap away.
 */

/** The ticket the page opens for a holding: the shape its table's Add and Exit pass. */
export interface HoldingOrder {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
}

export interface HoldingCardsProps {
  holdings: Holding[]
  /** The broker's own totals, shown until a price has moved. */
  stats: HoldingsStats | null
  bookAt: number | null
  loading: boolean
  error: string | null
  refreshing: boolean
  apiKey: string | null
  marketOpen: MarketOpen
  onRefresh: (showRefresh?: boolean) => unknown
  /** Opens the page's order ticket, as its table's Add (BUY) and Exit (SELL) do. */
  onOrder: (order: HoldingOrder) => unknown
  /** Whether the page's order ticket is open. */
  orderOpen: boolean
  onOrderClose: () => void
}

export function holdingId(holding: Pick<Holding, 'exchange' | 'symbol'>): string {
  return `${holding.exchange}:${holding.symbol}`
}

function feedRowOf(row: PricedRow<Holding>): FeedRow {
  return {
    exchange: row.item.exchange,
    ltp: row.ltp ?? undefined,
    quantity: row.item.quantity,
    _at: row.at,
    _live: row.live,
  }
}

/** The change since the previous close, in rupees and as a share of it, once every holding says. */
export function dayChangeOf(
  rows: PricedRow<Holding>[]
): { amount: number; percent: number | null } | null {
  let amount = 0
  let base = 0
  for (const row of rows) {
    const quantity = Number(row.item.quantity) || 0
    if (quantity === 0) continue
    if (row.ltp === null || row.prevClose === null) return null
    amount += (row.ltp - row.prevClose) * quantity
    base += row.prevClose * quantity
  }
  return { amount, percent: base > 0 ? (amount / base) * 100 : null }
}

/** The holdings' totals: the broker's until a price has moved, then from the prices shown. */
function totalsOf(rows: PricedRow<Holding>[], stats: HoldingsStats | null) {
  const moved = rows.some((row) => row.source !== 'rest')
  if (stats && !moved) return stats
  const priced = rows.map((row) => ({
    ...row.item,
    ltp: row.ltp ?? undefined,
    pnl: row.pnl ?? 0,
  }))
  return calculateLiveStats(priced, stats ?? {}) as HoldingsStats
}

export function HoldingCards(props: HoldingCardsProps) {
  const onRefresh = useLatest(props.onRefresh)
  const onOrder = useLatest(props.onOrder)
  const onOrderClose = useLatest(props.onOrderClose)
  return (
    <HoldingBoard {...props} onRefresh={onRefresh} onOrder={onOrder} onOrderClose={onOrderClose} />
  )
}

const HoldingBoard = memo(function HoldingBoard({
  holdings,
  stats,
  bookAt,
  loading,
  error,
  refreshing,
  apiKey,
  marketOpen,
  onRefresh,
  onOrder,
  orderOpen,
  onOrderClose,
}: HoldingCardsProps) {
  const { rows, feed } = useFramePrices({
    items: holdings,
    bookAt,
    marketOpen,
    apiKey,
    quotes: true,
  })
  const { line, now, stale } = useBookFeed(rows.map(feedRowOf), feed, marketOpen)
  const { overlay, open, swap, close } = useBookOverlay()
  const openSheet = useCallback((id: string) => open({ act: 'sheet', row: id }), [open])
  const refresh = useCallback(() => void onRefresh(true), [onRefresh])
  const figuresStale = (line.kind === 'delayed' || line.kind === 'connecting') && line.stale

  const target =
    overlay?.row != null ? (rows.find((row) => holdingId(row.item) === overlay.row) ?? null) : null

  // The page's ticket and the address move together: Back closes the ticket,
  // and the ticket closing (placed, cancelled) takes its entry away.
  const setTicket = useCallback(
    (next: boolean) => {
      if (!next) onOrderClose()
    },
    [onOrderClose]
  )
  const orderOpening = usePageDialog({
    entered: overlay?.act === 'buy' || overlay?.act === 'sell',
    open: orderOpen,
    setOpen: setTicket,
    close,
  })

  // A sheet over a holding the book no longer has is closed once the book is known.
  const orphan = overlay?.act === 'sheet' && !loading && target === null
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  const order = (row: PricedRow<Holding>, action: 'BUY' | 'SELL') => {
    orderOpening()
    void onOrder({
      symbol: row.item.symbol,
      exchange: row.item.exchange,
      action,
      quantity: action === 'BUY' ? 1 : row.item.quantity,
    })
    swap({ act: action === 'BUY' ? 'buy' : 'sell', row: holdingId(row.item) })
  }

  let content: ReactNode
  if (holdings.length === 0 && loading) {
    content = <LoadingCards />
  } else if (holdings.length === 0 && error) {
    content = <ProblemCard message="Holdings could not be read just now." onRetry={refresh} />
  } else if (holdings.length === 0) {
    content = (
      <EmptyCard
        title="No holdings"
        hint="Shares you take delivery of show here the day after you buy them."
      />
    )
  } else {
    const totals = totalsOf(rows, stats)
    content = (
      <>
        <Summary
          current={figure(totals.totalholdingvalue)}
          invested={figure(totals.totalinvvalue)}
          pnl={figure(totals.totalprofitandloss)}
          percent={figure(totals.totalpnlpercentage)}
          day={dayChangeOf(rows)}
          stale={figuresStale}
          line={line}
          now={now}
        />
        {error && <StaleBookNote text="Could not refresh just now. Showing the last figures." />}
        <ul aria-label="Holdings" className="flex flex-col gap-2">
          {rows.map((row) => {
            const quantity = Number(row.item.quantity) || 0
            return (
              <HoldingCard
                key={holdingId(row.item)}
                id={holdingId(row.item)}
                symbol={row.item.symbol}
                exchange={row.item.exchange}
                quantity={quantity}
                average={figure(row.item.average_price)}
                ltp={row.ltp}
                tick={row.tick}
                pnl={row.pnl}
                pnlPercent={row.pnlPercent}
                value={row.ltp === null ? null : row.ltp * quantity}
                dayPercent={
                  row.ltp !== null && row.prevClose !== null
                    ? ((row.ltp - row.prevClose) / row.prevClose) * 100
                    : null
                }
                stale={stale(feedRowOf(row))}
                onOpen={openSheet}
              />
            )
          })}
        </ul>
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <BookHeading title="Holdings" refreshing={refreshing} onRefresh={refresh} />
      {content}
      {overlay?.act === 'sheet' && target && (
        <HoldingSheet
          row={target}
          stale={stale(feedRowOf(target))}
          line={line}
          now={now}
          onClose={close}
          onBuy={() => order(target, 'BUY')}
          onSell={() => order(target, 'SELL')}
        />
      )}
    </div>
  )
})

/** Current value as the one big figure, then what went in, the overall P&L and today's change. */
function Summary({
  current,
  invested,
  pnl,
  percent,
  day,
  stale,
  line,
  now,
}: {
  current: number | null
  invested: number | null
  pnl: number | null
  percent: number | null
  day: { amount: number; percent: number | null } | null
  stale: boolean
  line: FeedLine
  now: number
}) {
  return (
    <section
      aria-labelledby="sd-holdings-value"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex min-h-5 items-center justify-between gap-3">
        <h2
          id="sd-holdings-value"
          className="text-[0.84375rem] font-semibold text-muted-foreground"
        >
          Current value
        </h2>
        <LiveLine line={line} now={now} />
      </div>
      <p
        data-testid="holdings-value"
        data-stale={stale || undefined}
        className={cn('sd-display text-[2.375rem] leading-[1.05] font-bold', stale && STALE)}
      >
        {current === null ? '-' : formatRupees(current)}
        {stale && <span className="sr-only"> Stale</span>}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-[color:var(--divider)] pt-2">
        <div className="flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Invested</dt>
          <dd className="text-[0.9375rem] font-bold">
            {invested === null ? '-' : formatRupees(invested)}
          </dd>
        </div>
        <div className="flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Overall P&amp;L</dt>
          <dd className="flex flex-wrap items-baseline gap-x-1.5 text-[0.9375rem] font-bold">
            <Money value={pnl} stale={stale} />
            <SignedPercent value={percent} stale={stale} className="text-xs font-semibold" />
          </dd>
        </div>
        <div className="col-span-2 flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Day&apos;s change</dt>
          <dd className="flex flex-wrap items-baseline gap-x-1.5 text-[0.9375rem] font-bold">
            {day ? (
              <>
                <Money value={day.amount} stale={stale} />
                <SignedPercent
                  value={day.percent}
                  stale={stale}
                  className="text-xs font-semibold"
                />
              </>
            ) : (
              <span className="text-sm font-medium text-muted-foreground">
                Waiting for the previous close
              </span>
            )}
          </dd>
        </div>
      </dl>
    </section>
  )
}

/** One holding: redrawn only when its own figures change. */
const HoldingCard = memo(function HoldingCard({
  id,
  symbol,
  exchange,
  quantity,
  average,
  ltp,
  tick,
  pnl,
  pnlPercent,
  value,
  dayPercent,
  stale,
  onOpen,
}: {
  id: string
  symbol: string
  exchange: string
  quantity: number
  average: number | null
  ltp: number | null
  tick: TickDirection | null
  pnl: number | null
  pnlPercent: number | null
  value: number | null
  dayPercent: number | null
  stale: boolean
  onOpen: (id: string) => void
}) {
  const named = describe(symbol, exchange)
  return (
    <li data-stale={stale || undefined} data-holding={id}>
      <button type="button" aria-haspopup="dialog" onClick={() => onOpen(id)} className={CARD}>
        <span className="flex items-baseline justify-between gap-3">
          <span
            className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
            title={symbol}
          >
            {named.title}
          </span>
          <Money value={pnl} stale={stale} className="text-base leading-tight font-bold" />
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            Qty {units(quantity)} · Avg {price(average, exchange)}
          </span>
          <SignedPercent value={pnlPercent} stale={stale} className="text-xs font-semibold" />
        </span>
        <span className="flex items-baseline justify-between gap-3 text-[0.8125rem] leading-tight">
          <span className="flex min-w-0 items-baseline gap-1.5">
            <Last value={ltp} tick={tick} stale={stale} exchange={exchange} />
            {dayPercent !== null && (
              <span className="whitespace-nowrap text-xs font-medium text-[color:var(--text-tertiary)]">
                Day <SignedPercent value={dayPercent} stale={stale} className="font-semibold" />
              </span>
            )}
          </span>
          <span className="whitespace-nowrap text-xs font-medium text-[color:var(--text-tertiary)]">
            Value{' '}
            <span className={cn('font-semibold text-foreground', stale && STALE)}>
              {value === null ? '-' : formatRupees(value)}
            </span>
          </span>
        </span>
      </button>
    </li>
  )
})

/** One holding's sheet: Buy more and Sell through the page's ticket, its chart, its figures. */
function HoldingSheet({
  row,
  stale,
  line,
  now,
  onClose,
  onBuy,
  onSell,
}: {
  row: PricedRow<Holding>
  stale: boolean
  line: FeedLine
  now: number
  onClose: () => void
  onBuy: () => void
  onSell: () => void
}) {
  const holding = row.item
  const { symbol, exchange, product } = holding
  const named = describe(symbol, exchange)
  const quantity = Number(holding.quantity) || 0
  const average = figure(holding.average_price)
  const dayAmount =
    row.ltp !== null && row.prevClose !== null ? (row.ltp - row.prevClose) * quantity : null
  const dayPercent =
    row.ltp !== null && row.prevClose !== null
      ? ((row.ltp - row.prevClose) / row.prevClose) * 100
      : null
  const meaning = PRODUCT_MEANING[product as Product]
  // Analyze mode: Buy more and Sell go to the sandbox, and say so.
  const sandbox = useSandbox()

  return (
    <BottomSheet
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={named.title}
      description={named.detail}
      aside={
        <div className="flex flex-col items-end gap-0.5 pt-2">
          <Money
            value={row.pnl}
            stale={stale}
            className="sd-display text-[1.5rem] leading-none font-bold"
            arrowClassName="size-3"
          />
          <SignedPercent value={row.pnlPercent} stale={stale} className="text-sm font-semibold" />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <LiveLine line={line} now={now} className="flex" />
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={onBuy}
            className={cn(ACTION, 'font-bold', BUY_FILL)}
          >
            <Plus aria-hidden="true" className="size-4" />
            <ActionWords sandbox={sandbox}>Buy more</ActionWords>
          </button>
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={onSell}
            className={cn(ACTION, 'font-bold', SELL_FILL)}
          >
            <Minus aria-hidden="true" className="size-4" />
            <ActionWords sandbox={sandbox}>Sell</ActionWords>
          </button>
        </div>

        <SymbolChartLink symbol={symbol} exchange={exchange} className={cn(OUTLINED, 'w-full')}>
          <CandlestickChart aria-hidden="true" className="size-5" />
          Chart
        </SymbolChartLink>

        <Details
          rows={[
            { label: 'Quantity', value: units(quantity) },
            { label: 'Average price', value: price(average, exchange) },
            {
              label: 'Last price',
              value: (
                <Last
                  value={row.ltp}
                  tick={row.tick}
                  stale={stale}
                  exchange={exchange}
                  label={null}
                />
              ),
            },
            row.ltp !== null && {
              label: 'Current value',
              value: formatRupees(row.ltp * quantity),
            },
            average !== null && { label: 'Invested', value: formatRupees(average * quantity) },
            dayAmount !== null && {
              label: "Day's change",
              value: (
                <span className="inline-flex items-baseline gap-1.5">
                  <Money value={dayAmount} stale={stale} />
                  <SignedPercent value={dayPercent} stale={stale} className="text-xs" />
                </span>
              ),
            },
            row.prevClose !== null && {
              label: 'Previous close',
              value: price(row.prevClose, exchange),
            },
            { label: 'Product', value: meaning ? `${product} · ${meaning}` : product },
          ]}
        />
      </div>
    </BottomSheet>
  )
}
