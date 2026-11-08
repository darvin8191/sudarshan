import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { usePriceAccess } from '@/sudarshan/access'
import { AlertFormSheet, type AlertPrefill, toastAlertSet } from '@/sudarshan/alerts/AlertForm'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { type OrderRequest, SymbolSheet } from '@/sudarshan/watchlist/SymbolSheet'
import type { Hearing } from './push'
import { Results } from './Results'
import type { ScreenRow } from './types'
import type { Address } from './useScreenerAddress'

/**
 * The Results tab of an open screen: the live list, and the sheets a row opens. The symbol sheet,
 * its order ticket and its alert form are held in the address (`sheet=symbol&k=NSE:INFY`,
 * `sheet=order`, `sheet=alert`) so Back closes one at a time. A ticket or alert form with no
 * request behind it (a reload) closes itself rather than stand open over nothing.
 */
export function ResultsPanel({
  screen,
  address,
  hearing,
  openSheet,
  closeSheet,
}: {
  screen: ScreenRow
  address: Address
  hearing: Hearing
  openSheet: (sheet: string, extra?: Record<string, string>) => void
  closeSheet: () => void
}) {
  const { apiKey } = usePriceAccess()
  const navigate = useNavigate()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
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
      <MarketCalendarSource key={day} onChange={setMarket} />
      <Results
        screen={screen}
        apiKey={apiKey}
        isOpen={isOpen}
        hearing={hearing}
        openKey={sheet === 'symbol' ? address.key : null}
        onOpen={(key) => openSheet('symbol', { k: key })}
      >
        {({ row, feed, now }) => (
          <SymbolSheet
            row={row}
            listName={screen.spec.name}
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
        )}
      </Results>
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
