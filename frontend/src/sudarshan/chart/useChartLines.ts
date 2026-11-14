/**
 * The books behind the phone chart's lines (M4.7, C5): the positions and the
 * order book, read as the Positions and Orders pages read them, and the active
 * price alerts from the Alerts page's cached state.
 *
 * Read once when the chart opens and again by `useBookSync`'s rules: after
 * every order event (a fill, a rejection, a cancel, a modify), on a Socket.IO
 * reconnect, on coming back after 15 s, and every 45 s while the market is not
 * known to be shut. A book is not market data: this is the REST re-sync the
 * fork's rules allow. A pushed order update moves its line at once, before the
 * re-read confirms it. A position's P&L moves with the chart's own ticks
 * (`LineLabels`), never with a read.
 *
 * A read that fails keeps the lines from the last good one and says so: they
 * are drawn grey with "as of hh:mm:ss". Switching between Live and Analyze
 * drops the lines until the other mode's books are read, so the sandbox's
 * lines are never shown as the broker's.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { onModeChange, useThemeStore } from '@/stores/themeStore'
import { useMarketAlertsState } from '@/sudarshan/alerts/useMarketAlertsState'
import { isCovered } from '@/sudarshan/alerts/words'
import {
  foldOrderUpdate,
  frameFitsMode,
  holdPushed,
  noteOrderBook,
  pushedStatuses,
  useOrderUpdates,
} from '@/sudarshan/books/orderFold'
import { useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
import type { Order, Position } from '@/types/trading'
import { type ChartLines, linesOf } from './overlays'

const clock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

export interface ChartBooks {
  positions: Position[]
  orders: Order[]
  /** When the shown positions were asked for; null until a read has answered. */
  bookAt: number | null
  /** Whether the shown books are the sandbox's: the mode they were asked for in. */
  sandbox: boolean
  /** No read has answered yet. */
  loading: boolean
  /** The last read failed: the books are as of `bookAt`. */
  failed: boolean
  /** Read both books again now. */
  refresh: () => Promise<void>
}

export function useChartBooks({
  apiKey,
  enabled,
  marketOpen,
}: {
  apiKey: string | null
  /** False for an instrument that is never traded (an index): nothing is read. */
  enabled: boolean
  /** False while the market is known to be shut, which holds the 45 s re-read. */
  marketOpen: () => boolean | null
}): ChartBooks {
  const appMode = useThemeStore((s) => s.appMode)
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [bookAt, setBookAt] = useState<number | null>(null)
  const [sandbox, setSandbox] = useState(false)
  const [failed, setFailed] = useState(false)
  const beginRead = useLatestRead()
  const on = enabled && apiKey !== null

  const refresh = useCallback(async () => {
    if (!on || apiKey === null) return
    const isLatest = beginRead()
    const askedAt = Date.now()
    const askedIn = useThemeStore.getState().appMode
    const [held, book] = await Promise.allSettled([
      tradingApi.getPositions(apiKey),
      tradingApi.getOrders(apiKey),
    ])
    if (!isLatest()) return
    const heldOk =
      held.status === 'fulfilled' && held.value.status === 'success' && !!held.value.data
    const bookOk =
      book.status === 'fulfilled' && book.value.status === 'success' && !!book.value.data
    if (heldOk) setPositions(held.value.data ?? [])
    if (bookOk) {
      const read = book.value.data?.orders ?? []
      noteOrderBook(read, askedAt)
      setOrders(holdPushed(read, pushedStatuses(), Date.now()))
    }
    if (heldOk && bookOk) {
      setBookAt(askedAt)
      setSandbox(askedIn === 'analyzer')
    }
    setFailed(!(heldOk && bookOk))
  }, [on, apiKey, beginRead])

  useEffect(() => {
    void refresh()
  }, [refresh])
  useBookSync(refresh, { enabled: on, marketOpen })
  // The broker's own update moves its order's line at once; the re-read confirms it.
  useOrderUpdates((frame) => {
    if (frameFitsMode(frame, appMode)) setOrders((current) => foldOrderUpdate(current, frame))
  }, on)
  // The other mode's books are other books: nothing of this one's is shown meanwhile.
  const refreshNow = useRef(refresh)
  refreshNow.current = refresh
  useEffect(
    () =>
      onModeChange(() => {
        beginRead()
        setPositions([])
        setOrders([])
        setBookAt(null)
        setFailed(false)
        void refreshNow.current()
      }),
    [beginRead]
  )

  return { positions, orders, bookAt, sandbox, loading: bookAt === null, failed, refresh }
}

/** The charted instrument's lines, from its books and its active price alerts. */
export function useChartLines({
  symbol,
  exchange,
  decimals,
  books,
}: {
  symbol: string
  exchange: string
  decimals: number
  books: ChartBooks
}): ChartLines {
  const alerts = useMarketAlertsState(isCovered(exchange)).data?.alerts
  const { positions, orders, bookAt, failed } = books
  return useMemo(
    () =>
      linesOf({
        symbol,
        exchange,
        decimals,
        positions: bookAt === null ? [] : positions,
        orders: bookAt === null ? [] : orders,
        alerts: alerts ?? [],
        staleNote: failed && bookAt !== null ? `as of ${clock.format(new Date(bookAt))}` : null,
      }),
    [symbol, exchange, decimals, positions, orders, bookAt, failed, alerts]
  )
}
