/**
 * One leg's own sheet on the options screen (M11.1, design 5.5): the watchlist's symbol sheet, as
 * it is, for the call or the put of a strike, reached from the strike sheet. It gives the leg's
 * chart, a market alert on it, its five-level depth, "Add to watchlist" (`AddToWatchlist.tsx`), and
 * Buy and Sell through the app's own ticket. Buy and Sell stay shut until the leg's lot size is
 * read, as on every symbol sheet: the sheet reads it itself and never takes the chain's figure for
 * an order. No order code here: an entry point, not an order path.
 */
import { lazy, Suspense } from 'react'
import { useNavigate } from 'react-router'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import { toastAlertSet } from '@/sudarshan/alerts/AlertForm'
import { describe } from '@/sudarshan/watchlist/rows'
import { type OrderRequest, SymbolSheet } from '@/sudarshan/watchlist/SymbolSheet'
import { AddToWatchlist } from './AddToWatchlist'
import type { ChainModel } from './model'
import { contractOf, type Feed, liveRow } from './StrikeEntry'

const AlertFormSheet = lazy(() =>
  import('@/sudarshan/alerts/AlertForm').then((module) => ({ default: module.AlertFormSheet }))
)

export function LegSheets({
  model,
  expiry,
  leg,
  adding,
  alert,
  apiKey,
  feed,
  onClose,
  onOrder,
  onAlert,
  onAddToList,
}: {
  model: ChainModel
  expiry: string
  /** The leg whose sheet is open, if one is. */
  leg: { strike: number; side: 'ce' | 'pe' } | null
  /** The leg being added to a watchlist, if that sheet is open. */
  adding: { strike: number; side: 'ce' | 'pe' } | null
  /** The alert form open over the page, if one is. */
  alert: AlertPrefill | null
  apiKey: string | null
  feed: Feed
  onClose: () => void
  onOrder: (order: OrderRequest) => void
  onAlert: (alert: AlertPrefill) => void
  /** Open "Add to watchlist" for the leg whose sheet is open. */
  onAddToList: (leg: { strike: number; side: 'ce' | 'pe' }) => void
}) {
  const navigate = useNavigate()
  const view = leg
    ? (model.rows.find((each) => each.strike === leg.strike)?.[leg.side] ?? null)
    : null
  const row = view ? liveRow(contractOf(view, expiry), view, model.asOf, feed) : null
  const added = adding
    ? (model.rows.find((each) => each.strike === adding.strike)?.[adding.side] ?? null)
    : null
  return (
    <>
      <SymbolSheet
        row={row}
        listName="Options"
        apiKey={apiKey}
        marketOpen={feed.marketOpen}
        feed={feed.feed}
        now={feed.now}
        onClose={onClose}
        onOrder={onOrder}
        onAlert={onAlert}
        onAddToList={leg ? () => onAddToList(leg) : undefined}
      />
      {added && (
        <AddToWatchlist
          symbol={added.symbol}
          exchange={added.exchange}
          label={describe(added.symbol, added.exchange).title}
          onClose={onClose}
        />
      )}
      {alert && (
        <Suspense fallback={null}>
          <AlertFormSheet
            prefill={alert}
            onClose={onClose}
            onSaved={(saved) => {
              onClose()
              toastAlertSet(saved, navigate)
            }}
          />
        </Suspense>
      )}
    </>
  )
}
