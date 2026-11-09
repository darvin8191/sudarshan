/**
 * What the Upstox tab reads and when (M7.1, cross-design P18).
 *
 * The service reads Upstox; the page asks the service for the parts of the shown view: on open,
 * after an `order_update` that can change them (the way books/useBookSync.ts does it, one read per
 * burst, once more when the broker has caught up), on a reconnect, on coming back after 15 seconds,
 * and as a slow re-sync of account state while the market is open and the page is shown. Never on
 * an `analyzer_update`: the sandbox's orders are not in the real Upstox account. Prices, values
 * and P&L between reads come only from ticks.
 */
import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef } from 'react'
import { holdingsMayChange, useBookSync } from '@/sudarshan/books/useBookSync'
import { PortfolioError, portfolioCall } from '../api'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import type { UpstoxBook, UpstoxPartName } from '../types'
import type { UpstoxView } from '../usePortfolioOverlay'

/** Holdings change slowly: a re-sync every 5 minutes. Positions and funds every 45 seconds. */
export const HOLDINGS_RESYNC_MS = 300_000
export const ACCOUNT_RESYNC_MS = 45_000
/** A part read this recently is answered from memory; an event asks for a read this fresh. */
export const EVENT_MAX_AGE_S = 2

/** The views that read the account book; History, Trades and P&L read their own records. */
export type BookView = Exclude<UpstoxView, 'history' | 'trades' | 'pnl'>

export const PARTS_OF: Record<BookView, UpstoxPartName[]> = {
  holdings: ['holdings'],
  positions: ['positions', 'mtf'],
  funds: ['funds'],
}

export function useUpstoxBook(view: BookView, marketOpen: () => boolean | null) {
  const maxAge = useRef<number | undefined>(undefined)
  const parts = PARTS_OF[view]
  const query = useQuery({
    queryKey: [...PORTFOLIO_KEY, 'upstox', view],
    queryFn: async (): Promise<UpstoxBook> => {
      const asked = maxAge.current
      maxAge.current = undefined
      const result = await portfolioCall('upstox_book', { parts, max_age: asked })
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const { refetch } = query
  const read = useCallback(() => {
    maxAge.current = EVENT_MAX_AGE_S
    return refetch()
  }, [refetch])
  useBookSync(read, {
    events: ['order_update'],
    resyncMs: view === 'holdings' ? HOLDINGS_RESYNC_MS : ACCOUNT_RESYNC_MS,
    marketOpen,
    // A holding changes only with an equity delivery order; positions and funds with any fill.
    accept: view === 'holdings' ? holdingsMayChange : undefined,
  })
  return query
}
