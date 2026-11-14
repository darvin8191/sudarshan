import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { noteGttBook, sentMarksNow } from '@/sudarshan/books/sent'
import type { GttOrder, Holding } from '@/types/trading'
import { PLACING, type Protect, protectShown, protectsOf } from './protect'
import { useProtectStage } from './stage'

/**
 * What the Holdings page knows about Protect (M5.6, G1): whether it is shown at all (the
 * server's switch, which arrives with the session facts) and the working sell GTTs, read from the
 * GTT book. Upstox pushes nothing for that book, so it is read when the page opens, whenever the
 * holdings themselves were read again (an order arrived, the 45 s re-sync, coming back to the
 * page) and after Protect's own actions. With the switch off nothing is read.
 */

/**
 * Tell the app-wide holds (books/sent.ts) what the GTT book now shows. A removal lets go when
 * its GTT is no longer working. A Protect being placed (held by its holding, `protect:NSE:X`)
 * lets go when the book shows a sell GTT on that holding; until then it stays held for as long
 * as its own clock says.
 */
function settle(sandbox: boolean, book: readonly GttOrder[]): void {
  const seen = book.map((gtt) => ({
    id: String(gtt.trigger_id),
    working: String(gtt.status).toLowerCase() === 'active',
  }))
  for (const mark of sentMarksNow().values()) {
    if (mark.kind !== 'gtt' || mark.sandbox !== sandbox || !mark.id.startsWith(PLACING)) continue
    const [exchange, ...rest] = mark.id.slice(PLACING.length).split(':')
    const holding = { exchange, symbol: rest.join(':'), quantity: 1, product: 'CNC' }
    seen.push({ id: mark.id, working: protectsOf(holding, book).length === 0 })
  }
  noteGttBook(sandbox, seen)
}

export interface ProtectBook {
  /** Whether Protect shows on this page now. */
  shown: boolean
  /** The GTT book has been read at least once since the page opened. */
  read: boolean
  /** The last read failed: what shows is from the read before it, or nothing. */
  failed: boolean
  of: (holding: Pick<Holding, 'symbol' | 'exchange' | 'quantity' | 'product'>) => Protect[]
  refresh: () => Promise<void>
}

export function useProtectBook(
  apiKey: string | null,
  sandbox: boolean,
  bookAt: number | null
): ProtectBook {
  const shown = protectShown(useProtectStage(), sandbox)
  const [gtts, setGtts] = useState<GttOrder[] | null>(null)
  const [failed, setFailed] = useState(false)
  const asked = useRef(0)

  const refresh = useCallback(async () => {
    if (!shown || !apiKey) return
    const mine = ++asked.current
    try {
      const response = await tradingApi.getGttOrderbook(apiKey, 'active')
      if (mine !== asked.current) return
      if (response.status === 'success') {
        const book = (response.data as GttOrder[]) ?? []
        setGtts(book)
        setFailed(false)
        settle(sandbox, book)
      } else setFailed(true)
    } catch {
      if (mine === asked.current) setFailed(true)
    }
  }, [shown, apiKey, sandbox])

  // biome-ignore lint/correctness/useExhaustiveDependencies: read again whenever the holdings were (bookAt), and when the book changes (Analyze mode)
  useEffect(() => {
    setGtts(null)
    void refresh()
  }, [refresh, sandbox])
  // biome-ignore lint/correctness/useExhaustiveDependencies: bookAt is the trigger, not an input
  useEffect(() => {
    void refresh()
  }, [bookAt])

  return useMemo(
    () => ({
      shown,
      read: gtts !== null,
      failed,
      of: (holding) => (shown && gtts ? protectsOf(holding, gtts) : []),
      refresh,
    }),
    [shown, gtts, failed, refresh]
  )
}
