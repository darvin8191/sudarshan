import { useQuery } from '@tanstack/react-query'
import { CandlestickChart, Clock, Layers, Trash2 } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router'
import { oiProfileApi } from '@/api/oi-profile'
import type { WatchlistItem } from '@/api/watchlist'
import { useMarketData } from '@/hooks/useMarketData'
import type { DepthLevel } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { formatAmount, formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { type FeedLine, isStale, keyOf } from '@/sudarshan/dashboard/live'
import { type Contract, contractOf } from './api'
import { BottomSheet } from './BottomSheet'
import { chainOf, describe, isIndex } from './rows'
import type { LiveRow } from './useLiveRows'
import { Figures, figuresOf } from './WatchlistRows'

/**
 * The phone chart screen for an instrument. That screen is another change in
 * flight; until it lands, this link opens the app's not-found page.
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
}: {
  level: DepthLevel | undefined
  side: 'bid' | 'ask'
  stale: boolean
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
      {shown ? formatPrice(level.price) : '—'}
    </td>,
  ]
  return <>{side === 'bid' ? cells : cells.reverse()}</>
}

/**
 * Five levels of the order book, tick by tick: the instrument's own Depth
 * subscription on the shared socket, open only while the sheet is.
 */
function Depth({
  item,
  marketOpen,
  feedTickAt,
  now,
}: {
  item: WatchlistItem
  marketOpen: boolean | null
  feedTickAt: number | null
  now: number
}) {
  const symbols = useMemo(
    () => [{ symbol: item.symbol, exchange: item.exchange }],
    [item.symbol, item.exchange]
  )
  const { data } = useMarketData({ symbols, mode: 'Depth' })
  const line = data.get(keyOf(item.symbol, item.exchange))
  const depth = line?.data.depth
  const stale = isStale(
    {
      ltp: depth ? 1 : null,
      prevClose: null,
      at: line?.lastUpdate ?? null,
      live: line?.updateSource === 'websocket',
    },
    now,
    marketOpen,
    feedTickAt
  )

  return (
    <section aria-labelledby="sd-depth-title" className="flex flex-col gap-1">
      <div className="flex min-h-8 items-center justify-between">
        <h3 id="sd-depth-title" className="text-sm font-bold">
          Market depth
        </h3>
        {stale && (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--warning-text)]">
            <Clock aria-hidden="true" className="size-3.5" />
            Stale
          </span>
        )}
      </div>
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
              <Level level={depth?.buy?.[index]} side="bid" stale={stale} />
              <Level level={depth?.sell?.[index]} side="ask" stale={stale} />
            </tr>
          ))}
        </tbody>
      </table>
      {!depth && (
        <p className="text-xs font-medium text-muted-foreground">Waiting for the order book.</p>
      )}
    </section>
  )
}

/**
 * The sheet a row opens: the instrument's level and change, Buy and Sell
 * (the app's order ticket, `PlaceOrderDialog`), its chart, its option chain
 * when it has one, five levels of depth, and taking it out of the list. An
 * index has no order book and cannot be traded, so it gets the chart and the
 * option chain only.
 */
export function SymbolSheet({
  row,
  listName,
  apiKey,
  line,
  marketOpen,
  feedTickAt,
  now,
  onClose,
  onOrder,
  onRemove,
}: {
  row: LiveRow | null
  listName: string
  apiKey: string | null
  line: FeedLine | null
  marketOpen: boolean | null
  feedTickAt: number | null
  now: number
  onClose: () => void
  onOrder: (item: WatchlistItem, action: 'BUY' | 'SELL', contract: Contract) => void
  onRemove: (item: WatchlistItem) => void
}) {
  if (!row) return null
  return (
    <Sheet
      row={row}
      listName={listName}
      apiKey={apiKey}
      line={line}
      marketOpen={marketOpen}
      feedTickAt={feedTickAt}
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
  line,
  marketOpen,
  feedTickAt,
  now,
  onClose,
  onOrder,
  onRemove,
}: Parameters<typeof SymbolSheet>[0] & { row: LiveRow }) {
  const { item } = row
  const named = describe(item.symbol, item.exchange)
  const tradable = !isIndex(item.exchange)
  const chain = useOptionChain(item)
  const contract = useQuery({
    queryKey: ['sudarshan', 'contract', item.exchange, item.symbol],
    queryFn: () => contractOf(apiKey ?? '', item.symbol, item.exchange),
    enabled: tradable && apiKey !== null,
    staleTime: 6 * 60 * 60_000,
    retry: false,
  })

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
          <Figures {...figuresOf(row)} size="sheet" />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        {line && <LiveLine line={line} now={now} className="flex" />}

        {tradable && (
          <div className="flex flex-col gap-1.5">
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                disabled={!contract.data}
                onClick={() => contract.data && onOrder(item, 'BUY', contract.data)}
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
                onClick={() => contract.data && onOrder(item, 'SELL', contract.data)}
                className={cn(
                  ACTION,
                  'bg-[color:var(--sell-solid)] font-bold tracking-wide text-white hover:opacity-90 disabled:opacity-50'
                )}
              >
                SELL
              </button>
            </div>
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

        {tradable && apiKey !== null && (
          <Depth item={item} marketOpen={marketOpen} feedTickAt={feedTickAt} now={now} />
        )}

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
