// SUDARSHAN P6 D5 (M6.7): the trade book for a family viewer. `TradeCards` takes this path when
// it is given `readOnly`. One card per fill, newest first. The owner's card is a link to the
// chart, an owner page; here a card is only a card. A figure the member's mask removed shows as
// Hidden, never as 0 and never worked out from what is left.
import { type ReactNode, useMemo } from 'react'
import { istTime } from '@/sudarshan/books/time'
import { formatRupees } from '@/sudarshan/dashboard/format'
import { type MaskedTrade, momentOfRow, tradeFigures } from '@/sudarshan/family/ordersView'
import { describe } from '@/sudarshan/watchlist/rows'
import { units } from './format'
import { Hidden } from './Hidden'
import { EmptyCard, LoadingCards, ProblemCard, price, SideChip } from './parts'
import { NOT_SHARED } from './ReadOnlyOrders'
import { type ReadOnlyBookProps, ReadStamp, ServiceWords, ViewerNote } from './readOnlyParts'

export type ReadOnlyTradesProps = ReadOnlyBookProps<MaskedTrade>

const NOTE = "The account owner's trades. Nothing here can be changed from this view."

export function ReadOnlyTrades({
  rows,
  readAt,
  hidden,
  state,
  words,
  loading,
  error,
  onRetry,
}: ReadOnlyTradesProps) {
  const sorted = useMemo(() => [...rows].sort((a, b) => momentOfRow(b) - momentOfRow(a)), [rows])
  const buys = rows.filter((row) => String(row.transaction_type).toUpperCase() === 'BUY').length
  const notShared = state === 'not_shared' || hidden.includes('orders')

  let content: ReactNode
  if (notShared) {
    content = <EmptyCard title={NOT_SHARED} />
  } else if (rows.length === 0 && loading) {
    content = <LoadingCards />
  } else if (rows.length === 0 && error) {
    content = <ProblemCard message={error} onRetry={onRetry} />
  } else if (rows.length === 0) {
    content = (
      <>
        <ServiceWords state={state} words={words} />
        <EmptyCard title="No trades today" hint="Every fill shows here as it happens." />
      </>
    )
  } else {
    content = (
      <>
        <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {rows.length} {rows.length === 1 ? 'trade' : 'trades'} · {buys} buy · {rows.length - buys}{' '}
          sell
        </p>
        <ServiceWords state={state} words={words} />
        {error && <ProblemCard message={error} onRetry={onRetry} />}
        <ul aria-label="Trades" className="flex flex-col gap-2">
          {sorted.map((row) => (
            <TradeCard key={row.trade_id} row={row} />
          ))}
        </ul>
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <ViewerNote name="trades" text={NOTE} />
      {content}
      {!notShared && <ReadStamp part="Trades" readAt={readAt} closed={state === 'closed'} />}
    </div>
  )
}

function TradeCard({ row }: { row: MaskedTrade }) {
  const exchange = row.exchange ?? ''
  const named = describe(row.symbol ?? '', exchange)
  const figures = tradeFigures(row)
  const moment = momentOfRow(row)
  return (
    <li
      data-trade={row.trade_id}
      className="flex min-h-14 w-full flex-col gap-1 rounded-[1rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
    >
      <span className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <SideChip side={row.transaction_type} />
          <span
            className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
            title={row.symbol ?? ''}
          >
            {named.title}
          </span>
        </span>
        {moment > 0 && (
          <span className="shrink-0 text-xs font-medium text-[color:var(--text-tertiary)]">
            {istTime(moment)}
          </span>
        )}
      </span>
      <span className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
          {named.detail} · {row.product}
        </span>
        <span className="flex shrink-0 items-baseline gap-1 text-[0.8125rem] font-bold whitespace-nowrap">
          {figures.quantity === undefined ? (
            <Hidden />
          ) : figures.quantity === null ? (
            '-'
          ) : (
            units(figures.quantity)
          )}
          <span className="font-medium text-[color:var(--text-secondary)]">at</span>
          {figures.price === undefined ? <Hidden /> : price(figures.price, exchange)}
        </span>
      </span>
      <span className="flex items-center justify-end gap-1 text-xs font-medium text-[color:var(--text-tertiary)]">
        Value{' '}
        {figures.value === undefined ? (
          <Hidden />
        ) : figures.value === null ? (
          '-'
        ) : (
          formatRupees(figures.value)
        )}
      </span>
    </li>
  )
}
