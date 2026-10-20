import { CandlestickChart, Pencil, X, XCircle } from 'lucide-react'
import { memo, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { type BookOrder, isFinalStatus } from '@/sudarshan/books/orderFold'
import type { TickDirection } from '@/sudarshan/books/prices'
import { SymbolChartLink } from '@/sudarshan/chart/SymbolChartLink'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { FeedLine, MarketOpen } from '@/sudarshan/dashboard/live'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import type { Order } from '@/types/trading'
import { clockOf, figure, momentOf, type OrderState, orderState, units } from './format'
import {
  ActionWords,
  CARD,
  Confirm,
  Details,
  EmptyCard,
  Last,
  LoadingCards,
  OUTLINED,
  ProblemCard,
  price,
  RefreshIcon,
  SANDBOX_SENTENCE,
  SentChip,
  SideChip,
  StaleBookNote,
  StatusChip,
} from './parts'
import { useBookOverlay, usePageDialog } from './useBookOverlay'
import { type PricedRow, useFramePrices } from './useFramePrices'
import { useHeld, useLatest } from './useLatest'
import { useSent } from './useSent'

/**
 * The order book on a phone (M6.9): filter chips, one card per order with its
 * status in words, the last price of the working ones tick by tick, and a sheet
 * per order. Modify opens the page's own modify dialog and Cancel makes its own
 * cancel call; Cancel all is its cancel-all, after the same confirmation. The
 * rows themselves are the page's: the broker's pushes already change them the
 * moment they arrive (`orderFold`), and its re-reads confirm them.
 */

export type OrderFilter = 'all' | 'open' | 'executed' | 'rejected' | 'cancelled'

const FILTERS: Array<{ value: OrderFilter; label: string; empty: string }> = [
  { value: 'all', label: 'All', empty: 'No orders today' },
  { value: 'open', label: 'Open', empty: 'No open orders' },
  { value: 'executed', label: 'Executed', empty: 'No executed orders' },
  { value: 'rejected', label: 'Rejected', empty: 'No rejected orders' },
  { value: 'cancelled', label: 'Cancelled', empty: 'No cancelled orders' },
]

export interface OrderCardsProps {
  orders: Order[]
  loading: boolean
  error: string | null
  refreshing: boolean
  apiKey: string | null
  marketOpen: MarketOpen
  onRefresh: (showRefresh?: boolean) => unknown
  /** The page's cancel call, the one its table's button makes. */
  onCancel: (orderid: string) => unknown
  /** The page's cancel-all call. */
  onCancelAll: () => unknown
  /** Opens the page's own modify dialog on this order. */
  onModify: (order: Order) => unknown
  /** Whether the page's modify dialog is open. */
  modifyOpen: boolean
  onModifyOpenChange: (open: boolean) => void
}

/**
 * "30/65": filled of ordered, once something is known to have filled (a
 * pushed frame, or the sandbox's book), and a complete order in full. The
 * quantity alone otherwise: Upstox's REST book says nothing of fills (M6.9),
 * and "0/65" would claim to know that nothing has.
 */
export function filledText(order: BookOrder): { text: string; filled: boolean } {
  const total = figure(order.quantity) ?? 0
  if (orderState(order.order_status).filter === 'executed') {
    return { text: `${units(total)}/${units(total)}`, filled: true }
  }
  const filled = figure(order.filled_quantity)
  if (filled !== null && filled > 0) {
    return { text: `${units(filled)}/${units(total)}`, filled: true }
  }
  return { text: units(total), filled: false }
}

/** What a cancel stops: "the remaining 60 of 100" of a part-filled order, else the quantity. */
export function remainingText(order: BookOrder): string {
  const total = figure(order.quantity) ?? 0
  const filled = figure(order.filled_quantity)
  if (filled === null || filled <= 0 || filled >= total) return units(total)
  const pending = figure(order.pending_quantity)
  const rest =
    pending !== null && pending > 0 && pending <= total - filled ? pending : total - filled
  return `the remaining ${units(rest)} of ${units(total)}`
}

/** "Limit 168.00", "Trigger 305.00 · Limit 310.00", with the average once something filled. */
export function priceText(order: BookOrder): string {
  const type = String(order.pricetype ?? '').toUpperCase()
  const at = (value: unknown) => price(figure(value), order.exchange)
  let text: string
  switch (type) {
    case 'MARKET':
      text = 'Market'
      break
    case 'LIMIT':
      text = `Limit ${at(order.price)}`
      break
    case 'SL':
      text = `Trigger ${at(order.trigger_price)} · Limit ${at(order.price)}`
      break
    case 'SL-M':
      text = `SL-M · Trigger ${at(order.trigger_price)}`
      break
    default:
      text = type || 'Order'
  }
  const average = figure(order.average_price)
  if (average !== null && average > 0) text += ` · Avg ${at(average)}`
  return text
}

function feedRowOf(row: PricedRow<{ symbol: string; exchange: string }>): FeedRow {
  return { exchange: row.item.exchange, ltp: row.ltp ?? undefined, _at: row.at, _live: row.live }
}

export function OrderCards(props: OrderCardsProps) {
  const onRefresh = useLatest(props.onRefresh)
  const onCancel = useLatest(props.onCancel)
  const onCancelAll = useLatest(props.onCancelAll)
  const onModify = useLatest(props.onModify)
  const onModifyOpenChange = useLatest(props.onModifyOpenChange)
  return (
    <OrderBoard
      {...props}
      onRefresh={onRefresh}
      onCancel={onCancel}
      onCancelAll={onCancelAll}
      onModify={onModify}
      onModifyOpenChange={onModifyOpenChange}
    />
  )
}

const OrderBoard = memo(function OrderBoard({
  orders,
  loading,
  error,
  refreshing,
  apiKey,
  marketOpen,
  onRefresh,
  onCancel,
  onCancelAll,
  onModify,
  modifyOpen,
  onModifyOpenChange,
}: OrderCardsProps) {
  const appMode = useThemeStore((state) => state.appMode)
  const [filter, setFilter] = useState<OrderFilter>('all')
  const refresh = useCallback(() => void onRefresh(true), [onRefresh])

  // Newest first, as the wide book opens.
  const sorted = useMemo(
    () =>
      [...(orders as BookOrder[])].sort((a, b) => momentOf(b.timestamp) - momentOf(a.timestamp)),
    [orders]
  )
  // The last price of every instrument with a working order, as the wide book shows it.
  const working = useMemo(() => {
    const seen = new Map<string, { symbol: string; exchange: string }>()
    for (const order of orders) {
      if (isFinalStatus(order.order_status)) continue
      seen.set(`${order.exchange}:${order.symbol}`, {
        symbol: order.symbol,
        exchange: order.exchange,
      })
    }
    return [...seen.values()]
  }, [orders])
  const { rows: prices, feed } = useFramePrices({
    items: working,
    bookAt: null,
    marketOpen,
    apiKey,
    quotes: true,
  })
  const { line, now, stale } = useBookFeed(prices.map(feedRowOf), feed, marketOpen)
  const priceOf = new Map(prices.map((row) => [`${row.item.exchange}:${row.item.symbol}`, row]))

  const { overlay, open, swap, close } = useBookOverlay()
  const openSheet = useCallback((orderid: string) => open({ act: 'sheet', row: orderid }), [open])

  // A cancel sent holds the order's Cancel and Modify until it shows a final
  // status (pushed or read), or SENT_HOLD_MS pass.
  const { marks: cancels, hold: holdCancels, settle: settleCancels } = useSent<true>()
  useEffect(() => {
    if (cancels.size === 0) return
    const working = new Set(
      orders.filter((order) => !isFinalStatus(order.order_status)).map((order) => order.orderid)
    )
    settleCancels([...cancels.keys()].filter((orderid) => !working.has(orderid)))
  }, [orders, cancels, settleCancels])

  const states = new Map(sorted.map((order) => [order.orderid, orderState(order.order_status)]))
  const counts: Record<OrderFilter, number> = {
    all: sorted.length,
    open: 0,
    executed: 0,
    rejected: 0,
    cancelled: 0,
  }
  // What Cancel all would cancel: the working orders whose cancel has not been sent.
  const cancellableIds: string[] = []
  for (const [orderid, state] of states) {
    if (state.filter) counts[state.filter] += 1
    if (state.working && !cancels.has(orderid)) cancellableIds.push(orderid)
  }
  const cancellable = cancellableIds.length
  const shown =
    filter === 'all'
      ? sorted
      : sorted.filter((order) => states.get(order.orderid)?.filter === filter)

  const target =
    overlay?.row != null ? (sorted.find((order) => order.orderid === overlay.row) ?? null) : null
  const targetState = target ? orderState(target.order_status) : null

  // Something open over an order the book no longer has, or a cancel for one
  // that is no longer working, is closed once the book is known. A modify
  // stays: the page's own dialog is open and says what the broker answers.
  const orphan =
    overlay !== null &&
    !loading &&
    (overlay.act === 'cancelall'
      ? false
      : overlay.act === 'modify'
        ? false
        : target === null || (overlay.act === 'cancel' && !targetState?.working))
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  // The page's modify dialog and the address move together: Back closes the
  // dialog, and the dialog closing (sent, cancelled) takes its entry away.
  const modifyOpening = usePageDialog({
    entered: overlay?.act === 'modify',
    open: modifyOpen,
    setOpen: onModifyOpenChange,
    close,
  })

  const who = appMode === 'analyzer' ? 'Sandbox' : 'Broker'
  // Analyze mode: the book is the sandbox's, and so is every cancel and modify from it.
  const sandbox = appMode === 'analyzer'
  const cancelling = overlay?.act === 'cancel' && targetState?.working ? target : null
  const cancelShown = useHeld(cancelling)

  let content: ReactNode
  if (orders.length === 0 && loading) {
    content = <LoadingCards />
  } else if (orders.length === 0 && error) {
    content = <ProblemCard message="The order book could not be read just now." onRetry={refresh} />
  } else {
    content = (
      <>
        {error && <StaleBookNote text="Could not refresh just now. Showing the last orders." />}
        {shown.length === 0 ? (
          <EmptyCard
            title={FILTERS.find((each) => each.value === filter)?.empty ?? 'No orders'}
            hint={filter === 'all' ? 'Orders you place today show here.' : undefined}
          />
        ) : (
          <ul aria-label="Orders" className="flex flex-col gap-2">
            {shown.map((order) => {
              const state = states.get(order.orderid) ?? orderState(order.order_status)
              const live = state.working
                ? priceOf.get(`${order.exchange}:${order.symbol}`)
                : undefined
              return (
                <OrderCard
                  key={order.orderid}
                  orderid={order.orderid}
                  symbol={order.symbol}
                  exchange={order.exchange}
                  action={order.action}
                  product={order.product}
                  quantity={filledText(order).text}
                  filled={filledText(order).filled}
                  prices={priceText(order)}
                  state={state}
                  time={clockOf(order.timestamp)}
                  ltp={live ? live.ltp : null}
                  tick={live ? live.tick : null}
                  stale={live ? stale(feedRowOf(live)) : false}
                  showLtp={live !== undefined}
                  sent={cancels.has(order.orderid)}
                  onOpen={openSheet}
                />
              )
            })}
          </ul>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-end gap-2" data-testid="order-actions">
        <RefreshIcon refreshing={refreshing} onRefresh={refresh} />
        <button
          type="button"
          disabled={cancellable === 0}
          aria-haspopup="dialog"
          onClick={() => open({ act: 'cancelall', row: null })}
          className={cn(
            OUTLINED,
            'shrink-0 whitespace-nowrap border-[color:var(--down)] text-[color:var(--down)]'
          )}
        >
          <X aria-hidden="true" className="size-4" />
          Cancel all{cancellable > 0 ? ` (${cancellable})` : ''}
        </button>
      </div>
      {/* The feed's line on a row of its own: "Reconnecting · updated 12s ago" needs the width. */}
      {working.length > 0 && (
        <p className="-mt-1 flex min-h-5 min-w-0 flex-wrap items-center gap-x-1.5 px-1">
          <span className="text-xs font-semibold text-muted-foreground">LTP</span>
          <LiveLine line={line} now={now} />
        </p>
      )}

      <fieldset className="-mx-4 min-w-0">
        <legend className="sr-only">Show orders</legend>
        <div className="flex gap-1.5 overflow-x-auto px-4 [scrollbar-width:none]">
          {FILTERS.map((each) => {
            const on = filter === each.value
            return (
              <button
                key={each.value}
                type="button"
                aria-pressed={on}
                onClick={() => setFilter(each.value)}
                className="group flex min-h-12 shrink-0 items-center outline-none"
              >
                <span
                  className={cn(
                    'flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
                    on
                      ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                      : 'border-input bg-card font-semibold text-[color:var(--text-secondary)]'
                  )}
                >
                  {each.label}
                  <span className="tabular-nums">{counts[each.value]}</span>
                </span>
              </button>
            )
          })}
        </div>
      </fieldset>

      {content}

      {overlay?.act === 'sheet' && target && targetState && (
        <OrderSheet
          order={target}
          state={targetState}
          live={
            targetState.working ? priceOf.get(`${target.exchange}:${target.symbol}`) : undefined
          }
          stale={stale}
          line={line}
          now={now}
          who={who}
          sandbox={sandbox}
          sent={cancels.has(target.orderid)}
          onClose={close}
          onModify={() => {
            modifyOpening()
            void onModify(target)
            swap({ act: 'modify', row: target.orderid })
          }}
          onCancel={() => swap({ act: 'cancel', row: target.orderid })}
        />
      )}

      <Confirm
        open={cancelling !== null}
        title="Cancel this order?"
        description={
          cancelShown
            ? `${cancelShown.action} ${remainingText(cancelShown)} ${describe(cancelShown.symbol, cancelShown.exchange).title} · ${priceText(cancelShown)}. ${sandbox ? `The order stops working in the sandbox. ${SANDBOX_SENTENCE}` : 'The order stops working at the exchange.'}`
            : ''
        }
        cancelLabel="Keep order"
        confirmLabel="Cancel order"
        onConfirm={() => {
          if (!cancelling) return
          const { orderid } = cancelling
          void holdCancels([[orderid, true]], () => onCancel(orderid))
        }}
        onClose={close}
      />

      <Confirm
        open={overlay?.act === 'cancelall'}
        title="Cancel all open orders?"
        description={`This will cancel all ${cancellable} open ${cancellable === 1 ? 'order' : 'orders'}${sandbox ? ` in the sandbox. ${SANDBOX_SENTENCE}` : '. This cannot be undone.'}`}
        cancelLabel="Keep orders"
        confirmLabel="Cancel all"
        onConfirm={() =>
          void holdCancels(
            cancellableIds.map((orderid) => [orderid, true]),
            () => onCancelAll()
          )
        }
        onClose={close}
      />
    </div>
  )
})

/** One order: redrawn only when its own figures change. */
const OrderCard = memo(function OrderCard({
  orderid,
  symbol,
  exchange,
  action,
  product,
  quantity,
  filled,
  prices,
  state,
  time,
  ltp,
  tick,
  stale,
  showLtp,
  sent,
  onOpen,
}: {
  orderid: string
  symbol: string
  exchange: string
  action: string
  product: string
  quantity: string
  filled: boolean
  prices: string
  state: OrderState
  time: string
  ltp: number | null
  tick: TickDirection | null
  stale: boolean
  showLtp: boolean
  /** Its cancel went, and the order has not ended yet. */
  sent: boolean
  onOpen: (orderid: string) => void
}) {
  const named = describe(symbol, exchange)
  return (
    <li data-order={orderid} data-status={state.label}>
      <button type="button" aria-haspopup="dialog" onClick={() => onOpen(orderid)} className={CARD}>
        {/* The whole name, always: a long one moves the status to a line of its own. */}
        <span className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className="flex min-w-0 max-w-full items-center gap-2">
            <SideChip side={action} />
            <span
              className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
              title={symbol}
            >
              {named.title}
            </span>
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            {sent && <SentChip label="Cancel sent" />}
            <StatusChip state={state} />
          </span>
        </span>
        <span className="flex items-baseline justify-between gap-3 text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
          <span className="min-w-0 truncate">
            {named.detail} · {product}
          </span>
          <span className="shrink-0">{time}</span>
        </span>
        <span className="flex items-start justify-between gap-3 text-[0.8125rem] leading-tight">
          <span className="min-w-0">
            <span className="font-bold">
              <span className="sr-only">{filled ? 'Filled ' : 'Quantity '}</span>
              {quantity}
            </span>
            <span className="text-[color:var(--text-secondary)]"> · {prices}</span>
          </span>
          {showLtp && (
            <Last value={ltp} tick={tick} stale={stale} exchange={exchange} className="shrink-0" />
          )}
        </span>
      </button>
    </li>
  )
})

/** One order's sheet: Modify and Cancel while it works, the broker's reason when refused, its chart. */
function OrderSheet({
  order,
  state,
  live,
  stale,
  line,
  now,
  who,
  sandbox,
  sent,
  onClose,
  onModify,
  onCancel,
}: {
  order: BookOrder
  state: OrderState
  live: PricedRow<{ symbol: string; exchange: string }> | undefined
  stale: (row: FeedRow) => boolean
  line: FeedLine
  now: number
  who: 'Broker' | 'Sandbox'
  /** Analyze mode: Modify and Cancel go to the sandbox, and say so. */
  sandbox: boolean
  /** Its cancel went, and the order has not ended yet: Modify and Cancel wait. */
  sent: boolean
  onClose: () => void
  onModify: () => void
  onCancel: () => void
}) {
  const { symbol, exchange, product } = order
  const named = describe(symbol, exchange)
  const reason = String(order.rejection_reason ?? '').trim()
  const pending = figure(order.pending_quantity)
  const average = figure(order.average_price)
  const meaning = PRODUCT_MEANING[product as Product]

  return (
    <BottomSheet
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={named.title}
      description={named.detail}
      aside={
        <div className="pt-2.5">
          <StatusChip state={state} />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-sm">
            <SideChip side={order.action} />
            <span className="font-bold">{filledText(order).text}</span>
            <span className="text-[color:var(--text-secondary)]">{order.pricetype}</span>
          </span>
          {live && (
            <Last
              value={live.ltp}
              tick={live.tick}
              stale={stale(feedRowOf(live))}
              exchange={exchange}
              className="text-sm"
            />
          )}
        </div>
        {live && <LiveLine line={line} now={now} className="flex" />}

        {state.working && (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              aria-haspopup="dialog"
              disabled={sent}
              onClick={onModify}
              className={cn(OUTLINED, 'border-primary text-primary')}
            >
              <Pencil aria-hidden="true" className="size-4" />
              <ActionWords sandbox={sandbox && !sent}>Modify</ActionWords>
            </button>
            <button
              type="button"
              aria-haspopup="dialog"
              disabled={sent}
              onClick={onCancel}
              className={cn(OUTLINED, 'border-[color:var(--down)] text-[color:var(--down)]')}
            >
              <X aria-hidden="true" className="size-4" />
              {sent ? 'Cancel sent' : <ActionWords sandbox={sandbox}>Cancel order</ActionWords>}
            </button>
          </div>
        )}

        {state.tone === 'down' && (
          <p
            data-testid="rejection-reason"
            className="flex items-start gap-2 rounded-[0.875rem] bg-[color:var(--down-tint)] px-3 py-2.5 text-sm font-medium text-foreground"
          >
            <XCircle
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--down)]"
            />
            <span>
              {reason ? (
                <>
                  <span className="font-bold">{who} says:</span> {reason}
                </>
              ) : (
                `The ${who.toLowerCase()} gave no reason.`
              )}
            </span>
          </p>
        )}

        <SymbolChartLink symbol={symbol} exchange={exchange} className={cn(OUTLINED, 'w-full')}>
          <CandlestickChart aria-hidden="true" className="size-5" />
          Chart
        </SymbolChartLink>

        <Details
          rows={[
            { label: 'Order ID', value: order.orderid },
            { label: 'Placed at', value: clockOf(order.timestamp) || '-' },
            { label: 'Quantity', value: filledText(order).text },
            pending !== null && state.working && { label: 'Pending', value: units(pending) },
            { label: 'Price', value: priceText(order) },
            average !== null &&
              average > 0 && { label: 'Average price', value: price(average, exchange) },
            { label: 'Product', value: meaning ? `${product} · ${meaning}` : product },
          ]}
        />
      </div>
    </BottomSheet>
  )
}
