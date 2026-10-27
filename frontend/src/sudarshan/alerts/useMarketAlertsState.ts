import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { type AlertsError, type MarketAlertsState, marketAlertsApi } from '@/api/marketAlerts'
import { bindMarketAlertsCache } from './marketAlerts'
import { STATE_KEY } from './stateCache'

/**
 * The Alerts page's state (contract 1.1): read when the page opens, then kept current by what the
 * server pushes, which marketAlerts.ts applies to this cache, and read again only where that says
 * to, after a reconnect, or when the phone comes back to a page older than half a minute. Never on
 * a timer. A failed read is shown at once, in the server's words, rather than tried again.
 */
export function useMarketAlertsState() {
  const client = useQueryClient()
  useEffect(() => bindMarketAlertsCache(client), [client])
  return useQuery<MarketAlertsState, AlertsError>({
    queryKey: STATE_KEY,
    queryFn: marketAlertsApi.state,
    staleTime: 30_000,
    retry: false,
  })
}
