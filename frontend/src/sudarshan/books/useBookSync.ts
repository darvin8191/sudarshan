import { useCallback, useEffect, useRef } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import type { OrderEventType } from '@/hooks/useOrderEventRefresh'
import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { AWAY_MS, useOnReturn } from './useReturn'

/**
 * When a page reads its books (orders, trades, positions, holdings) from the
 * broker again (M6.1 to M6.4). The model is the live dashboard's re-read
 * (pages/Dashboard.tsx); this is the same rules for every other book:
 *
 * - **After every order event**, including `order_update`, which carries the
 *   broker's fills, rejections and square-offs and which nothing but the
 *   dashboard listened to. A burst (one order passing through four states in a
 *   second, the legs of a basket) becomes one read: it waits for a short
 *   quiet, never longer than a second after the first event. An
 *   `order_update` is read once more a little later, because the broker's
 *   order and position books can trail its own push by a second or two.
 * - **When the Socket.IO connection comes back**, since anything pushed while
 *   it was down went to nobody.
 * - **On coming back to the page** after more than 15 seconds away.
 * - **Every 45 seconds while a market is open** (or not known to be shut),
 *   so the figures never drift far from the broker's, events or not.
 *
 * One read at a time: anything that asks during a read queues a single read
 * after it. A read the page starts itself (first load, Refresh, a mode
 * change) runs beside these, which is why every read is also guarded by
 * `useLatestRead`, so an answer that a newer read has overtaken is dropped.
 */

/** Every event after which a book may have changed. */
export const BOOK_EVENTS: readonly OrderEventType[] = [
  'order_event',
  'analyzer_update',
  'close_position_event',
  'cancel_order_event',
  'modify_order_event',
  'order_update',
]

export { AWAY_MS }

/** An event is answered after this much quiet... */
export const SETTLE_MS = 400

/** ...and never later than this after the first event of a burst. */
export const MAX_WAIT_MS = 1_000

/** A broker order update is read once more this long after the last one. */
export const CONFIRM_MS = 2_500

export interface BookSyncOptions {
  enabled?: boolean
  events?: readonly OrderEventType[]
  /** How often to re-read while a market is open; null for no periodic re-read. */
  resyncMs?: number | null
  /** How long away from the page makes coming back re-read; null for never. */
  awayMs?: number | null
  /** Whether a market is open: false holds the periodic re-read, null (not known) does not. */
  marketOpen?: () => boolean | null
}

const NOT_KNOWN = () => null

export function useBookSync(
  read: () => unknown,
  options: BookSyncOptions = {}
): () => Promise<void> {
  const {
    enabled = true,
    events = BOOK_EVENTS,
    resyncMs = RESYNC_MS,
    awayMs = AWAY_MS,
    marketOpen = NOT_KNOWN,
  } = options
  const { socket } = useSocketContext()

  const readRef = useRef(read)
  const marketOpenRef = useRef(marketOpen)
  useEffect(() => {
    readRef.current = read
    marketOpenRef.current = marketOpen
  }, [read, marketOpen])

  const reading = useRef(false)
  const queued = useRef(false)
  const sync = useCallback(async () => {
    if (reading.current) {
      queued.current = true
      return
    }
    reading.current = true
    try {
      do {
        queued.current = false
        try {
          await readRef.current()
        } catch {
          // The page's read reports its own failure; the next trigger tries again.
        }
      } while (queued.current)
    } finally {
      reading.current = false
    }
  }, [])

  const eventsKey = events.join('|')

  // Order events, debounced into one read per burst, and the reconnect.
  // biome-ignore lint/correctness/useExhaustiveDependencies: events are keyed by content (eventsKey); callers pass inline arrays
  useEffect(() => {
    if (!enabled || !socket) return
    let settle: ReturnType<typeof setTimeout> | null = null
    let confirm: ReturnType<typeof setTimeout> | null = null
    let burstStartedAt = 0

    const answer = () => {
      settle = null
      void sync()
    }
    const onEvent = () => {
      const now = Date.now()
      if (settle) clearTimeout(settle)
      else burstStartedAt = now
      const wait = Math.max(0, Math.min(SETTLE_MS, burstStartedAt + MAX_WAIT_MS - now))
      settle = setTimeout(answer, wait)
    }
    const onBrokerUpdate = () => {
      onEvent()
      if (confirm) clearTimeout(confirm)
      confirm = setTimeout(() => {
        confirm = null
        void sync()
      }, CONFIRM_MS)
    }
    const onReconnect = () => void sync()

    const subscribed = [...events]
    for (const event of subscribed) {
      socket.on(event, event === 'order_update' ? onBrokerUpdate : onEvent)
    }
    socket.on('connect', onReconnect)
    return () => {
      for (const event of subscribed) {
        socket.off(event, event === 'order_update' ? onBrokerUpdate : onEvent)
      }
      socket.off('connect', onReconnect)
      if (settle) clearTimeout(settle)
      if (confirm) clearTimeout(confirm)
    }
  }, [socket, eventsKey, enabled, sync])

  // Back on the page after a while away.
  useOnReturn(() => void sync(), awayMs, enabled)

  // The slow re-sync while a market is open (or not known to be shut).
  useEffect(() => {
    if (!enabled || resyncMs === null) return
    const timer = setInterval(() => {
      if (document.visibilityState !== 'visible') return
      if (marketOpenRef.current() === false) return
      void sync()
    }, resyncMs)
    return () => clearInterval(timer)
  }, [enabled, resyncMs, sync])

  return sync
}

/**
 * A guard for a page's reads: call it as a read starts, and ask the function
 * it returns, when the answer arrives, whether the read is still the newest.
 * An answer a newer read has overtaken is dropped, so an old book never
 * replaces a newer one on screen.
 */
export function useLatestRead(): () => () => boolean {
  const latest = useRef(0)
  return useCallback(() => {
    const mine = ++latest.current
    return () => latest.current === mine
  }, [])
}
