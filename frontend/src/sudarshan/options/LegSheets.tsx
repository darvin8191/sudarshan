/**
 * One leg's own sheet on the options screen (M11.1, design 5.5): the watchlist's symbol sheet, as
 * it is, for the call or the put of a strike, reached from the strike sheet. It gives the leg's
 * chart, a market alert on it, its five-level depth, and Buy and Sell through the app's own ticket.
 * No order code here: an entry point, not an order path.
 */
import { lazy, Suspense } from 'react'
import { useNavigate } from 'react-router'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import { toastAlertSet } from '@/sudarshan/alerts/AlertForm'
import { type OrderRequest, SymbolSheet } from '@/sudarshan/watchlist/SymbolSheet'
import type { ChainModel } from './model'
import { contractOf, type Feed, liveRow } from './StrikeEntry'

const AlertFormSheet = lazy(() =>
  import('@/sudarshan/alerts/AlertForm').then((module) => ({ default: module.AlertFormSheet }))
)

export function LegSheets({
  model,
  expiry,
  leg,
  alert,
  apiKey,
  feed,
  onClose,
  onOrder,
  onAlert,
}: {
  model: ChainModel
  expiry: string
  /** The leg whose sheet is open, if one is. */
  leg: { strike: number; side: 'ce' | 'pe' } | null
  /** The alert form open over the page, if one is. */
  alert: AlertPrefill | null
  apiKey: string | null
  feed: Feed
  onClose: () => void
  onOrder: (order: OrderRequest) => void
  onAlert: (alert: AlertPrefill) => void
}) {
  const navigate = useNavigate()
  const view = leg
    ? (model.rows.find((each) => each.strike === leg.strike)?.[leg.side] ?? null)
    : null
  const row = view ? liveRow(contractOf(view, expiry), view, model.asOf, feed) : null
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
      />
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
