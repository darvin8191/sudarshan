import { useQuery } from '@tanstack/react-query'
import { CandlestickChart, Clock, Layers, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { oiProfileApi } from '@/api/oi-profile'
import type { WatchlistItem } from '@/api/watchlist'
import type { DepthLevel } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { formatAmount, formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { ago, keyOf, STALE_MS } from '@/sudarshan/dashboard/live'
import { type FeedState, useFrameLines, useMarketDataManager } from '@/sudarshan/live/useFrameLines'
import {
  PRODUCT_MEANING,
  type Product,
  productsFor,
  readProduct,
  rememberProduct,
} from '@/sudarshan/ticket/productPref'
import { type Contract, contractOf } from './api'
import { BottomSheet } from './BottomSheet'
import { chainOf, describe, isIndex, priceDecimals, statusLine } from './rows'
import type { LiveRow } from './useLiveRows'
import { Figures, figuresOf } from './WatchlistRows'

/**
 * The phone chart screen for an instrument: the same path the chart screen's
 * own `chartPath` builds (`sudarshan/chart/prefs.ts` on the live-charts
 * change), registered as `/chart/:exchange/:symbol`.
 */
export function chartPath(symbol: string, exchange: string): string {
  return `/chart/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}`
}

/** The option chain tool, opened on an underlying. */
export function chainPath(chain: { exchange: string; underlying: string }): string {
  const query = new URLSearchParams({ exchange: chain.exchange, underlying: chain.underlying })
  return `/optionchain?${query}`
}

const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
const OUTLINED = cn(ACTION, 'border border-input bg-card text-foreground hover:bg-accent')

/**
 * The option chain an instrument opens, when it has one: its underlying must
 * be in the exchange's list of underlyings with options, read once a session.
 */
function useOptionChain(item: WatchlistItem) {
  const chain = chainOf(item.symbol, item.exchange)
  const listed = useQuery({
    queryKey: ['sudarshan', 'option-underlyings', chain?.exchange],
    queryFn: () => oiProfileApi.getUnderlyings(chain?.exchange ?? ''),
    enabled: chain !== null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })
  if (!chain || listed.data?.status !== 'success') return null
  return listed.data.underlyings.includes(chain.underlying) ? chain : null
}

/** One side of one level: quantity then price for a bid, price then quantity for an ask. */
function Level({
  level,
  side,
  stale,
  decimals,
}: {
  level: DepthLevel | undefined
  side: 'bid' | 'ask'
  stale: boolean
  decimals: number
}) {
  const shown = level !== undefined && level.price > 0
  const tone = stale
    ? 'text-[color:var(--stale)]'
    : side === 'bid'
      ? 'text-[color:var(--buy-solid)]'
      : 'text-[color:var(--sell-solid)]'
  const cells = [
    <td
      key="qty"
      className={cn(
        'px-2 py-2',
        stale ? 'text-[color:var(--stale)]' : 'text-[color:var(--text-secondary)]'
      )}
    >
      {shown ? formatAmount(level.quantity, 0) : '—'}
    </td>,
    <td key="price" className={cn('px-2 py-2 font-semibold', shown && tone)}>
      {shown ? formatPrice(level.price, decimals) : '—'}
    </td>,
  ]
  return <>{side === 'bid' ? cells : cells.reverse()}</>
}

/**
 * Five levels of the order book, tick by tick, on the instrument's own Depth
 * subscription, open only while the sheet is.
 *
 * The book the shared cache line already holds when the sheet opens is from an
 * earlier visit: LTP ticks keep that line moving but carry no book, and after a
 * reconnect only the list's LTP subscriptions are sent again. So nothing is
 * shown until a depth frame arrives after the sheet opens (the manager makes a
 * new book object for each one); with the market closed, the sheet says so
 * rather than waiting. Its age runs from the last change of the book,
 * not from the last LTP tick: a book that has not changed for `STALE_MS` while
 * the market is open shows how old it is, and is stale, greyed, once the
 * socket is down as well. While the socket is up an unchanged book is the
 * current one: the broker sends a new frame whenever it changes.
 */
function Depth({
  item,
  marketOpen,
  now,
}: {
  item: WatchlistItem
  marketOpen: boolean | null
  now: number
}) {
  const manager = useMarketDataManager()
  const [bookAtOpen] = useState(() => manager.getCachedData(item.symbol, item.exchange)?.data.depth)
  const instruments = useMemo(
    () => [{ symbol: item.symbol, exchange: item.exchange }],
    [item.symbol, item.exchange]
  )
  const { lines, feed } = useFrameLines({ instruments, mode: 'Depth' })
  const line = lines.get(keyOf(item.symbol, item.exchange))
  const depth = line?.data.depth

  const changed = useRef<{ book: unknown; at: number } | null>(null)
  if (depth && depth !== bookAtOpen && changed.current?.book !== depth) {
    changed.current = { book: depth, at: line?.lastUpdate ?? now }
  }
  const book = changed.current ? depth : undefined
  const age = changed.current ? now - changed.current.at : null
  const old = age !== null && marketOpen === true && age > STALE_MS
  const stale = old && !feed.connected
  const decimals = priceDecimals(item.exchange)

  return (
    <section aria-labelledby="sd-depth-title" className="flex flex-col gap-1">
      <div className="flex min-h-8 items-center justify-between gap-2">
        <h3 id="sd-depth-title" className="text-sm font-bold">
          Market depth
        </h3>
        {old && age !== null && (
          <span
            data-depth-age={stale ? 'stale' : 'old'}
            className={cn(
              'inline-flex items-center gap-1 text-xs font-semibold',
              stale ? 'text-[color:var(--warning-text)]' : 'text-muted-foreground'
            )}
          >
            <Clock aria-hidden="true" className="size-3.5" />
            {stale ? 'Stale · updated' : 'Updated'} {ago(age)}
          </span>
        )}
      </div>
      {book ? (
        <table
          data-stale={stale || undefined}
          className="w-full table-fixed border-collapse text-right text-sm"
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
            {[0, 1, 2, 3, 4].map((index) => (
              <tr key={index}>
                <Level level={book.buy?.[index]} side="bid" stale={stale} decimals={decimals} />
                <Level level={book.sell?.[index]} side="ask" stale={stale} decimals={decimals} />
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="py-1 text-sm font-medium text-muted-foreground">
          {marketOpen === false
            ? 'Market closed. Depth shows when it opens.'
            : 'Waiting for market depth.'}
        </p>
      )}
    </section>
  )
}

/**
 * Which product the order ticket opens with on this device for this segment
 * (`sudarshan/ticket/productPref`). The ticket's own default shows until one is
 * picked; the ticket itself can still change it for one order.
 */
function ProductPick({
  exchange,
  product,
  onPick,
}: {
  exchange: string
  product: Product | undefined
  onPick: (product: Product) => void
}) {
  const choices = productsFor(exchange)
  if (choices.length === 0) return null
  const current = product ?? choices[0]
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="pb-1 text-xs font-semibold text-[color:var(--text-secondary)]">
        The ticket opens with
      </legend>
      <div className="grid grid-cols-2 gap-1 rounded-[0.875rem] bg-[color:var(--surface-2)] p-1">
        {choices.map((choice) => {
          const on = choice === current
          return (
            <button
              key={choice}
              type="button"
              aria-pressed={on}
              onClick={() => onPick(choice)}
              className={cn(
                'flex min-h-12 flex-col items-center justify-center rounded-[0.6875rem] px-2 text-center outline-none focus-visible:ring-2 focus-visible:ring-ring',
                on
                  ? 'border border-primary bg-card font-bold text-primary shadow-[var(--shadow-card)]'
                  : 'font-semibold text-[color:var(--text-secondary)]'
              )}
            >
              <span className="text-sm leading-tight">{choice}</span>
              <span className="text-[0.6875rem] leading-tight font-medium">
                {PRODUCT_MEANING[choice]}
              </span>
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

export type OrderRequest = {
  item: WatchlistItem
  action: 'BUY' | 'SELL'
  contract: Contract
  /** Undefined until a product is picked on this device, so the ticket keeps its default. */
  product: Product | undefined
}

/**
 * The sheet a row opens: the instrument's level and change, its own status
 * line, Buy and Sell (the app's order ticket, `PlaceOrderDialog`), the product
 * the ticket opens with, its chart, its option chain when it has one, five
 * levels of depth, and taking it out of the list. An index has no order book
 * and cannot be traded, so it gets the chart and the option chain only.
 */
export function SymbolSheet({
  row,
  listName,
  apiKey,
  marketOpen,
  feed,
  now,
  onClose,
  onOrder,
  onRemove,
}: {
  row: LiveRow | null
  listName: string
  apiKey: string | null
  /** Whether this instrument's market is open. */
  marketOpen: boolean | null
  feed: FeedState
  now: number
  onClose: () => void
  onOrder: (order: OrderRequest) => void
  onRemove: (item: WatchlistItem) => void
}) {
  if (!row) return null
  return (
    <Sheet
      key={row.item.id}
      row={row}
      listName={listName}
      apiKey={apiKey}
      marketOpen={marketOpen}
      feed={feed}
      now={now}
      onClose={onClose}
      onOrder={onOrder}
      onRemove={onRemove}
    />
  )
}

function Sheet({
  row,
  listName,
  apiKey,
  marketOpen,
  feed,
  now,
  onClose,
  onOrder,
  onRemove,
}: Parameters<typeof SymbolSheet>[0] & { row: LiveRow }) {
  const { item } = row
  const named = describe(item.symbol, item.exchange)
  const tradable = !isIndex(item.exchange)
  const chain = useOptionChain(item)
  const [product, setProduct] = useState(() => readProduct(item.exchange))
  const contract = useQuery({
    queryKey: ['sudarshan', 'contract', item.exchange, item.symbol],
    queryFn: () => contractOf(apiKey ?? '', item.symbol, item.exchange),
    enabled: tradable && apiKey !== null,
    staleTime: 6 * 60 * 60_000,
    retry: false,
  })
  const line = statusLine({
    now,
    marketOpen,
    lastTickAt: row.tickAt,
    lastUpdateAt: row.view.at,
    connected: feed.connected,
    fallback: feed.fallback,
  })
  const order = (action: 'BUY' | 'SELL') => {
    if (contract.data) onOrder({ item, action, contract: contract.data, product })
  }

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={named.title}
      description={named.detail}
      aside={
        <div className="pt-1.5">
          <Figures {...figuresOf(row)} size="sheet" decimals={priceDecimals(item.exchange)} />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <LiveLine line={line} now={now} className="flex" />

        {tradable && (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                disabled={!contract.data}
                onClick={() => order('BUY')}
                className={cn(
                  ACTION,
                  'bg-[color:var(--buy-solid)] font-bold tracking-wide text-white hover:opacity-90 disabled:opacity-50'
                )}
              >
                BUY
              </button>
              <button
                type="button"
                disabled={!contract.data}
                onClick={() => order('SELL')}
                className={cn(
                  ACTION,
                  'bg-[color:var(--sell-solid)] font-bold tracking-wide text-white hover:opacity-90 disabled:opacity-50'
                )}
              >
                SELL
              </button>
            </div>
            {contract.isPending && apiKey !== null && (
              <p className="text-xs font-medium text-muted-foreground">Reading the lot size.</p>
            )}
            {contract.isError && (
              <p className="flex flex-wrap items-center gap-x-2 text-xs font-medium text-[color:var(--warning-text)]">
                The lot size could not be read, so the order ticket stays shut.
                <button
                  type="button"
                  onClick={() => void contract.refetch()}
                  className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
                >
                  Try again
                </button>
              </p>
            )}
            <ProductPick
              exchange={item.exchange}
              product={product}
              onPick={(picked) => {
                rememberProduct(item.exchange, picked)
                setProduct(picked)
              }}
            />
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <Link to={chartPath(item.symbol, item.exchange)} className={OUTLINED}>
            <CandlestickChart aria-hidden="true" className="size-5" />
            Chart
          </Link>
          {chain && (
            <Link to={chainPath(chain)} className={OUTLINED}>
              <Layers aria-hidden="true" className="size-5" />
              Option chain
            </Link>
          )}
        </div>

        {tradable && apiKey !== null && <Depth item={item} marketOpen={marketOpen} now={now} />}

        <button
          type="button"
          onClick={() => onRemove(item)}
          className={cn(ACTION, 'justify-start px-1 text-[color:var(--down)] hover:bg-accent')}
        >
          <Trash2 aria-hidden="true" className="size-5" />
          Remove from {listName}
        </button>
      </div>
    </BottomSheet>
  )
}
