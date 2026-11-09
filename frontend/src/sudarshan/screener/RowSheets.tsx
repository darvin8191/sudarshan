import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { AlertFormSheet, type AlertPrefill, toastAlertSet } from '@/sudarshan/alerts/AlertForm'
import type { MarketOpen } from '@/sudarshan/dashboard/live'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { type OrderRequest, SymbolSheet } from '@/sudarshan/watchlist/SymbolSheet'
import type { LiveRow } from '@/sudarshan/watchlist/useLiveRows'
import type { Address } from './useScreenerAddress'

/**
 * The sheets a dashboard row opens, held in the address so Back closes one at a time
 * (`sheet=symbol&k=NSE:INFY&s=<screen id>&wk=<kind>`, then `sheet=order` or `sheet=alert`): the
 * same ones the Results tab opens (ResultsPanel.tsx), over a row of a widget. A ticket or form
 * with no request behind it (a reload) closes itself rather than stand open over nothing.
 */
export function RowSheets({
  address,
  row,
  listName,
  apiKey,
  isOpen,
  feed,
  now,
  openSheet,
  closeSheet,
}: {
  address: Address
  row: LiveRow | null
  listName: string
  apiKey: string | null
  isOpen: MarketOpen
  feed: FeedState
  now: number
  openSheet: (sheet: string, extra?: Record<string, string>) => void
  closeSheet: () => void
}) {
  const navigate = useNavigate()
  const [order, setOrder] = useState<OrderRequest | null>(null)
  const [alert, setAlert] = useState<AlertPrefill | null>(null)
  const sheet = address.sheet
  const ordering = sheet === 'order' && order !== null
  const alerting = sheet === 'alert' && alert !== null
  const orphan = (sheet === 'order' && order === null) || (sheet === 'alert' && alert === null)
  useEffect(() => {
    if (orphan) closeSheet()
  }, [orphan, closeSheet])
  return (
    <>
      <SymbolSheet
        row={sheet === 'symbol' ? row : null}
        listName={listName}
        apiKey={apiKey}
        marketOpen={row ? isOpen(row.item.exchange) : null}
        feed={feed}
        now={now}
        onClose={closeSheet}
        onOrder={(request) => {
          setOrder(request)
          openSheet('order')
        }}
        onAlert={(request) => {
          setAlert(request)
          openSheet('alert')
        }}
      />
      <PlaceOrderDialog
        open={ordering}
        onOpenChange={(next) => {
          if (!next) closeSheet()
        }}
        symbol={order?.item.symbol ?? ''}
        exchange={order?.item.exchange ?? ''}
        action={order?.action ?? 'BUY'}
        lotSize={order?.contract.lotSize ?? 1}
        tickSize={order?.contract.tickSize ?? 0.05}
        product={order?.product}
        strategy="Screener"
      />
      {alerting && alert && (
        <AlertFormSheet
          prefill={alert}
          onClose={closeSheet}
          onSaved={(saved) => {
            closeSheet()
            toastAlertSet(saved, navigate)
          }}
        />
      )}
    </>
  )
}
