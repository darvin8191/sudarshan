import { useEffect } from 'react'
import { tradingApi } from '@/api/trading'
import { noteOrderBook } from './orderFold'
import { isHeld, marksOf, noteExitOrders, useSentMarks } from './sent'

/** How often the order book is read while an exit waits on its own order (after the first read). */
export const EXIT_ORDER_READ_MS = 30_000

/**
 * While an exit is held only by its own order still working (M6.9,
 * books/sent.ts: its time is up, and an order the broker pushed on its closing
 * side has not ended), read the order book at once and then every 30 s, so a
 * final status whose push was lost (a Socket.IO reconnect, a phone asleep)
 * still settles it, and a reload learns how its orders stand. An order book is
 * not market data: this is the REST re-sync the fork's rules allow, and it
 * stops as soon as nothing waits on it. The hold is never cut short by a
 * clock: a price-protected market order can rest unfilled.
 */
export function useExitOrderReads(sandbox: boolean, apiKey: string | null): void {
  const marks = useSentMarks()
  // Which exits wait on their orders: one more starts its own first read at once.
  const waiting = marksOf(marks, 'exit', sandbox)
    .filter((mark) => isHeld(mark) && mark.byOrder)
    .map((mark) => mark.id)
    .sort()
    .join('\n')

  useEffect(() => {
    if (!waiting || !apiKey) return
    let stopped = false
    const read = async () => {
      const askedAt = Date.now()
      try {
        const response = await tradingApi.getOrders(apiKey)
        if (stopped || response.status !== 'success' || !response.data) return
        const orders = response.data.orders ?? []
        noteOrderBook(orders, askedAt)
        noteExitOrders(sandbox, orders, askedAt)
      } catch {
        // Not read this time: the next read tries again, and the hold stays.
      }
    }
    void read()
    const every = setInterval(() => void read(), EXIT_ORDER_READ_MS)
    return () => {
      stopped = true
      clearInterval(every)
    }
  }, [waiting, apiKey, sandbox])
}
