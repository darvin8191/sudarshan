// SUDARSHAN P6 D5 (M6.7): the order book for a family viewer. `OrderCards` takes this path when
// it is given `readOnly`. It is a board of its own, not the owner's with buttons switched off,
// because the owner's reads owner endpoints (the access check, the sent-cancel holds, the
// sandbox mode) that a viewer phone must never reach.
//
// What it has not got: Modify, Cancel, Cancel all, the order ticket and the chart link (which
// leads to an owner page). Nothing here sends anything. What it has: every order with its status
// in an icon and a word, the broker's reason on a refused one, the last price of the working
// ones tick by tick, and a figure the member's mask removed shown as Hidden, never as 0.
import { XCircle } from 'lucide-react'
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { reasonOf } from '@/sudarshan/books/orderFold'
import { istTime } from '@/sudarshan/books/time'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { instrumentOf } from '@/sudarshan/family/books'
import {
  type MaskedOrder,
  momentOfRow,
  type OrderFigures,
  orderFigures,
  typeOf,
} from '@/sudarshan/family/ordersView'
import { useViewerLive } from '@/sudarshan/family/useViewerLive'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import { type OrderState, orderState, units } from './format'
import { Hidden } from './Hidden'
import {
  CARD,
  Details,
  EmptyCard,
  Last,
  LoadingCards,
  ProblemCard,
  price,
  SideChip,
  StatusChip,
} from './parts'
import {
  NotRealTime,
  type ReadOnlyBookProps,
  ReadStamp,
  ServiceWords,
  UnreadCard,
  unreadWords,
  ViewerNote,
} from './readOnlyParts'
import { useBookOverlay } from './useBookOverlay'

export type ReadOnlyOrdersProps = ReadOnlyBookProps<MaskedOrder>

export const NOT_SHARED = 'Not shared with you.'
const NOTE = "The account owner's orders. Nothing here can be changed from this view."

type Filter = 'all' | 'open' | 'executed' | 'rejected' | 'cancelled'

const FILTERS: Array<{ value: Filter; label: string; empty: string }> = [
  { value: 'all', label: 'All', empty: 'No orders today' },
  { value: 'open', label: 'Open', empty: 'No open orders' },
  { value: 'executed', label: 'Executed', empty: 'No executed orders' },
  { value: 'rejected', label: 'Rejected', empty: 'No rejected orders' },
  { value: 'cancelled', label: 'Cancelled', empty: 'No cancelled orders' },
]

const keyOf = (row: MaskedOrder) => {
  const instrument = instrumentOf(row)
  return instrument ? `${instrument.exchange.toUpperCase()}:${instrument.symbol.toUpperCase()}` : ''
}

/** A figure that may be hidden (absent), not known (null) or a price. */
function Priced({
  value,
  exchange,
}: {
  value: number | null | undefined
  exchange: string
}): ReactNode {
  if (value === undefined) return <Hidden />
  return price(value, exchange)
}

/** "Limit 168.00", "Trigger 305.00 · Limit 310.00", each price Hidden where the mask took it. */
function PriceWords({
  row,
  figures,
  withAverage = true,
}: {
  row: MaskedOrder
  figures: OrderFigures
  withAverage?: boolean
}) {
  const exchange = row.exchange ?? ''
  const type = typeOf(row)
  let words: ReactNode
  switch (type) {
    case 'MARKET':
      words = 'Market'
      break
    case 'LIMIT':
      words = (
        <>
          Limit <Priced value={figures.price} exchange={exchange} />
        </>
      )
      break
    case 'SL':
      words = (
        <>
          Trigger <Priced value={figures.trigger} exchange={exchange} /> · Limit{' '}
          <Priced value={figures.price} exchange={exchange} />
        </>
      )
      break
    case 'SL-M':
      words = (
        <>
          SL-M · Trigger <Priced value={figures.trigger} exchange={exchange} />
        </>
      )
      break
    default:
      words = type || 'Order'
  }
  const average = figures.average
  const showAverage =
    withAverage && (average === undefined ? state(row).filter === 'executed' : (average ?? 0) > 0)
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1">
      <span>{words}</span>
      {showAverage && (
        <span>
          · Avg <Priced value={average} exchange={exchange} />
        </span>
      )}
    </span>
  )
}

const state = (row: MaskedOrder): OrderState => orderState(row.status)

/**
 * "30/65" once something is known to have filled, a complete order in full, the quantity alone
 * otherwise (a broker's order book says nothing of fills until they happen): Hidden where the
 * mask took the quantity.
 */
function QuantityWords({ row, figures }: { row: MaskedOrder; figures: OrderFigures }) {
  const { total, filled } = figures
  if (total === undefined) return <Hidden />
  const whole = total ?? 0
  if (state(row).filter === 'executed') return <>{`${units(whole)}/${units(whole)}`}</>
  if (typeof filled === 'number' && filled > 0) return <>{`${units(filled)}/${units(whole)}`}</>
  return <>{units(whole)}</>
}

export function ReadOnlyOrders({
  rows,
  readAt,
  hidden,
  state: readState,
  words,
  loading,
  error,
  onRetry,
  marketOpen,
}: ReadOnlyOrdersProps) {
  const [filter, setFilter] = useState<Filter>('all')
  const sorted = useMemo(() => [...rows].sort((a, b) => momentOfRow(b) - momentOfRow(a)), [rows])
  const working = useMemo(
    () =>
      sorted
        .filter((row) => state(row).working)
        .map(instrumentOf)
        .filter((i): i is NonNullable<typeof i> => i !== null),
    [sorted]
  )
  const live = useViewerLive({ instruments: working, marketOpen })
  const { overlay, open, close } = useBookOverlay()

  const counts: Record<Filter, number> = {
    all: sorted.length,
    open: 0,
    executed: 0,
    rejected: 0,
    cancelled: 0,
  }
  for (const row of sorted) {
    const which = state(row).filter
    if (which) counts[which] += 1
  }
  const shown = filter === 'all' ? sorted : sorted.filter((row) => state(row).filter === filter)

  const target =
    overlay?.row != null ? (sorted.find((row) => row.order_id === overlay.row) ?? null) : null
  const orphan = overlay !== null && (overlay.act !== 'sheet' || (!loading && target === null))
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  const notShared = readState === 'not_shared' || hidden.includes('orders')
  const unread = unreadWords(readState, words, rows.length, readAt)

  let content: ReactNode
  if (notShared) {
    content = <EmptyCard title={NOT_SHARED} />
  } else if (rows.length === 0 && loading) {
    content = <LoadingCards />
  } else if (rows.length === 0 && error) {
    content = <ProblemCard message={error} onRetry={onRetry} />
  } else if (unread !== null) {
    // Verification N10 (v0.12.0): not a day without orders: the order book was not read (the
    // portfolio switched off, or the account out of reach). The service's sentence is the card,
    // with no filter and no count, as on Positions and Holdings (review M4).
    content = <UnreadCard text={unread} />
  } else {
    content = (
      <>
        <ServiceWords state={readState} words={words} />
        {error && <ProblemCard message={error} onRetry={onRetry} />}
        {working.length > 0 && (
          <p className="flex min-h-5 min-w-0 flex-wrap items-center gap-x-1.5 px-1">
            <span className="text-xs font-semibold text-muted-foreground">LTP</span>
            <LiveLine line={live.line} now={live.now} />
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
        {shown.length === 0 ? (
          <EmptyCard
            title={FILTERS.find((each) => each.value === filter)?.empty ?? 'No orders'}
            hint={
              filter === 'all' && rows.length === 0
                ? (words ?? 'Orders the account owner places today show here.')
                : undefined
            }
          />
        ) : (
          <ul aria-label="Orders" className="flex flex-col gap-2">
            {shown.map((row) => (
              <OrderCard
                key={row.order_id}
                row={row}
                ltp={live.ticks.get(keyOf(row))?.ltp ?? null}
                tick={live.directions.get(keyOf(row)) ?? null}
                stale={live.stale(instrumentOf(row))}
                capped={live.capped.has(keyOf(row))}
                readAt={readAt}
                onOpen={(id) => open({ act: 'sheet', row: id })}
              />
            ))}
          </ul>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <ViewerNote name="orders" text={NOTE} />
      {content}
      {!notShared && (
        <ReadStamp
          part="Orders"
          readAt={readAt}
          closed={readState === 'closed' || marketOpen('') === false}
        />
      )}
      {overlay?.act === 'sheet' && target && (
        <OrderSheet
          row={target}
          ltp={live.ticks.get(keyOf(target))?.ltp ?? null}
          stale={live.stale(instrumentOf(target))}
          line={live.line}
          now={live.now}
          onClose={close}
        />
      )}
    </div>
  )
}

function OrderCard({
  row,
  ltp,
  tick,
  stale,
  capped,
  readAt,
  onOpen,
}: {
  row: MaskedOrder
  ltp: number | null
  tick: 'up' | 'down' | null
  stale: boolean
  capped: boolean
  readAt: number | null
  onOpen: (id: string) => void
}) {
  const exchange = row.exchange ?? ''
  const named = describe(row.symbol ?? '', exchange)
  const figures = orderFigures(row)
  const status = state(row)
  const moment = momentOfRow(row)
  return (
    <li data-order={row.order_id} data-status={status.label}>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onOpen(row.order_id)}
        className={CARD}
      >
        <span className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className="flex min-w-0 max-w-full items-center gap-2">
            <SideChip side={row.transaction_type} />
            <span
              className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
              title={row.symbol ?? ''}
            >
              {named.title}
            </span>
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <StatusChip state={status} />
          </span>
        </span>
        <span className="flex items-baseline justify-between gap-3 text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
          <span className="min-w-0 truncate">
            {named.detail} · {row.product}
          </span>
          {moment > 0 && <span className="shrink-0">{istTime(moment)}</span>}
        </span>
        <span className="flex items-start justify-between gap-3 text-[0.8125rem] leading-tight">
          <span className="min-w-0">
            <span className="font-bold">
              <span className="sr-only">Quantity </span>
              <QuantityWords row={row} figures={figures} />
            </span>
            <span className="text-[color:var(--text-secondary)]">
              {' · '}
              <PriceWords row={row} figures={figures} />
            </span>
          </span>
          {status.working && !capped && (
            <Last value={ltp} tick={tick} stale={stale} exchange={exchange} className="shrink-0" />
          )}
        </span>
        {status.working && capped && <NotRealTime readAt={readAt} />}
      </button>
    </li>
  )
}

/** One order's sheet: what it is and what the broker says about it, and no action. */
function OrderSheet({
  row,
  ltp,
  stale,
  line,
  now,
  onClose,
}: {
  row: MaskedOrder
  ltp: number | null
  stale: boolean
  line: Parameters<typeof LiveLine>[0]['line']
  now: number
  onClose: () => void
}) {
  const exchange = row.exchange ?? ''
  const named = describe(row.symbol ?? '', exchange)
  const status = state(row)
  const figures = orderFigures(row)
  const reason = reasonOf(row.status_message)
  const meaning = PRODUCT_MEANING[row.product as Product]
  const moment = momentOfRow(row)
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
          <StatusChip state={status} />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-sm">
            <SideChip side={row.transaction_type} />
            <span className="font-bold">
              <QuantityWords row={row} figures={figures} />
            </span>
            <span className="text-[color:var(--text-secondary)]">{typeOf(row)}</span>
          </span>
          {status.working && (
            <Last value={ltp} tick={null} stale={stale} exchange={exchange} className="text-sm" />
          )}
        </div>
        {status.working && <LiveLine line={line} now={now} className="flex" />}

        {status.tone === 'down' && (
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
                  <span className="font-bold">Broker says:</span> {reason}
                </>
              ) : (
                'The broker gave no reason.'
              )}
            </span>
          </p>
        )}

        <Details
          rows={[
            { label: 'Order ID', value: row.order_id },
            moment > 0 && { label: 'Placed at', value: istTime(moment) },
            { label: 'Quantity', value: <QuantityWords row={row} figures={figures} /> },
            status.working &&
              figures.pending !== undefined &&
              figures.pending !== null && { label: 'Pending', value: units(figures.pending) },
            {
              label: 'Price',
              value: <PriceWords row={row} figures={figures} withAverage={false} />,
            },
            figures.average !== null &&
              (figures.average === undefined
                ? status.filter === 'executed'
                : figures.average > 0) && {
                label: 'Average price',
                value: <Priced value={figures.average} exchange={exchange} />,
              },
            {
              label: 'Product',
              value: meaning ? `${row.product} · ${meaning}` : row.product,
            },
          ]}
        />
      </div>
    </BottomSheet>
  )
}
