import { useCallback, useEffect, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { onModeChange } from '@/stores/themeStore'
import { holdingsMayChange, useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
import { anyOpen, type MarketOpen } from '@/sudarshan/dashboard/live'
import type { Holding, Position } from '@/types/trading'

/** What the owner holds and has open, as OpenAlgo's books last answered. Nothing is stored. */
export interface HeldBook {
  holdings: Holding[]
  positions: Position[]
}

const EXCHANGES = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX', 'CDS']

/**
 * F7: the holdings and the positions for the holdings line (M8.7), read through `useBookSync`
 * and nothing else: when the page opens with the switch on, after an order event (holdings only
 * after one that can change them), on a reconnect, on coming back to the page, and at the books'
 * own interval while a market is open. No timer of its own, no storage. With `enabled` false
 * (the switch is off, a family render, no key) nothing is read and nothing is held.
 */
export function useHeld({
  enabled,
  apiKey,
  marketOpen,
}: {
  enabled: boolean
  apiKey: string | null
  marketOpen: MarketOpen
}): HeldBook | null {
  const on = enabled && apiKey !== null
  const [holdings, setHoldings] = useState<Holding[] | null>(null)
  const [positions, setPositions] = useState<Position[] | null>(null)
  const alive = useRef(true)
  const beginHoldings = useLatestRead()
  const beginPositions = useLatestRead()
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const loadHoldings = useCallback(async () => {
    if (!apiKey) return
    const latest = beginHoldings()
    try {
      const response = await tradingApi.getHoldings(apiKey)
      if (!alive.current || !latest()) return
      const rows = response.data?.holdings
      setHoldings(response.status === 'success' && Array.isArray(rows) ? rows : null)
    } catch {
      if (alive.current && latest()) setHoldings(null)
    }
  }, [apiKey, beginHoldings])

  const loadPositions = useCallback(async () => {
    if (!apiKey) return
    const latest = beginPositions()
    try {
      const response = await tradingApi.getPositions(apiKey)
      if (!alive.current || !latest()) return
      setPositions(
        response.status === 'success' && Array.isArray(response.data) ? response.data : null
      )
    } catch {
      if (alive.current && latest()) setPositions(null)
    }
  }, [apiKey, beginPositions])

  // The first read, and again for a mode change: Live and Analyze hold different books.
  useEffect(() => {
    if (!on) {
      setHoldings(null)
      setPositions(null)
      return
    }
    void loadHoldings()
    void loadPositions()
    return onModeChange(() => {
      void loadHoldings()
      void loadPositions()
    })
  }, [on, loadHoldings, loadPositions])

  const open = useCallback(
    () => anyOpen(EXCHANGES.map((exchange) => marketOpen(exchange))),
    [marketOpen]
  )
  // Holdings answer only an update that can change them; positions answer every fill.
  useBookSync(loadHoldings, { enabled: on, marketOpen: open, accept: holdingsMayChange })
  useBookSync(loadPositions, { enabled: on, marketOpen: open })

  if (!on || (holdings === null && positions === null)) return null
  return { holdings: holdings ?? [], positions: positions ?? [] }
}
