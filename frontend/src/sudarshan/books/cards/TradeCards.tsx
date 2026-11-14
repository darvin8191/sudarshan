import { memo, type ReactNode, useCallback, useMemo } from 'react'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess, useReadWhenReady } from '@/sudarshan/access'
import type { Trade } from '@/types/trading'
import {
  BookHeading,
  EmptyCard,
  ExportIcon,
  exportBook,
  LoadingCards,
  ProblemCard,
  StaleBookNote,
} from './parts'
import { ReadOnlyTrades, type ReadOnlyTradesProps } from './ReadOnlyTrades'
import { groupTrades, TradeGroups } from './TradeGroups'
import { useLatest } from './useLatest'

/**
 * The trade book on a phone (M6.9): one card per order, newest first, with its
 * side, quantity, price and time, and each fill under it when it took several
 * (B3, TradeGroups). A trade is done, so there is nothing to do on it but look:
 * a tap opens its chart. Export saves the day's trade book as a CSV.
 */

/** The day's trade book as a CSV, from the app's own export route (B3). */
const exportTrades = () => exportBook('/tradebook/export', 'tradebook')

export interface TradeCardsProps {
  // SUDARSHAN P6 D5: the owner's board is never the read-only one (see TradeCards below).
  readOnly?: never
  trades: Trade[]
  loading: boolean
  error: string | null
  refreshing: boolean
  onRefresh: (showRefresh?: boolean) => unknown
}

function OwnerTradeCards(props: TradeCardsProps) {
  const onRefresh = useLatest(props.onRefresh)
  return <TradeBoard {...props} onRefresh={onRefresh} />
}

// SUDARSHAN P6 D5 (M6.7): `readOnly` is the viewer's path, a board of its own. A fill is done, so
// there is nothing to do on it, and the owner's card links to the chart, an owner page.
export function TradeCards(props: TradeCardsProps | ReadOnlyTradesProps) {
  if (props.readOnly) return <ReadOnlyTrades {...props} />
  return <OwnerTradeCards {...props} />
}

const TradeBoard = memo(function TradeBoard({
  trades,
  loading,
  error,
  refreshing,
  onRefresh,
}: TradeCardsProps) {
  const refresh = useCallback(() => void onRefresh(true), [onRefresh])
  // The trade book is read with the key too: without it or the Upstox login,
  // the notice says which, never "No trades today".
  const access = usePriceAccess()
  useReadWhenReady(access.state, error !== null, refresh)
  const groups = useMemo(() => groupTrades(trades), [trades])
  const buys = trades.filter((trade) => String(trade.action).toUpperCase() === 'BUY').length

  let content: ReactNode
  if (access.state !== 'ready') {
    content = <AccessNotice access={access} />
  } else if (trades.length === 0 && loading) {
    content = <LoadingCards />
  } else if (trades.length === 0 && error) {
    content = <ProblemCard message="The trade book could not be read just now." onRetry={refresh} />
  } else if (trades.length === 0) {
    content = <EmptyCard title="No trades today" hint="Every fill shows here as it happens." />
  } else {
    content = (
      <>
        <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {trades.length} {trades.length === 1 ? 'trade' : 'trades'}
          {groups.length < trades.length
            ? ` in ${groups.length} ${groups.length === 1 ? 'order' : 'orders'}`
            : ''}{' '}
          · {buys} buy · {trades.length - buys} sell
        </p>
        {error && <StaleBookNote text="Could not refresh just now. Showing the last trades." />}
        <TradeGroups groups={groups} />
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <BookHeading
        title="Trades"
        refreshing={refreshing}
        onRefresh={refresh}
        extra={<ExportIcon what="trade book" run={exportTrades} />}
      />
      {content}
    </div>
  )
})
