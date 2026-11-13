import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'
import { type AlertsError, type MarketAlertsState, marketAlertsApi } from '@/api/marketAlerts'
import { bindMarketAlertsCache, type Hearing, hearingNow, onHearing } from './marketAlerts'
import { STATE_KEY } from './stateCache'

/**
 * The Alerts page's state (contract 1.1): read when the page opens, then kept current by what the
 * server pushes, which marketAlerts.ts applies to this cache, and read again only where that says
 * to, after a reconnect, or when the phone comes back to a page older than half a minute. Never on
 * a timer. A failed read is shown at once, in the server's words, rather than tried again.
 *
 * `enabled` false reads nothing and binds nothing: the watchlist page passes its v2 switch, so with
 * the switch off it makes no request it did not make before (review H1).
 */
export function useMarketAlertsState(enabled = true) {
  const client = useQueryClient()
  useEffect(() => {
    if (enabled) bindMarketAlertsCache(client)
  }, [client, enabled])
  return useQuery<MarketAlertsState, AlertsError>({
    queryKey: STATE_KEY,
    queryFn: marketAlertsApi.state,
    staleTime: 30_000,
    retry: false,
    enabled,
  })
}

/**
 * Whether this screen hears the owner's room, where the alert service pushes (marketAlerts.ts):
 * `joined` from the join's ack until the connection drops; `unknown` before the app has a
 * connection at all.
 */
export function useAlertsHearing(): Hearing {
  return useSyncExternalStore(onHearing, hearingNow, () => 'unknown')
}
