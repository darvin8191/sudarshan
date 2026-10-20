import { useCallback, useEffect, useRef } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import type { OrderEventType } from '@/hooks/useOrderEventRefresh'
import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { recentPush } from './orderFold'
import { connectionCount } from './sharedSocket'
import { AWAY_MS } from './useReturn'

/**
 * When a page reads its books (orders, trades, positions, holdings) from the
 * broker again (M6.1 to M6.4). The model is the live dashboard's re-read
 * (pages/Dashboard.tsx); this is the same rules for every other book:
 *
 * - **After every order event**, including `order_update`, which carries the
 *   broker's fills, rejections and square-offs. A burst (one order passing
 *   through four states in a second, the legs of a basket) becomes one read:
 *   it waits for a short quiet, never longer than a second after the first
 *   event. A page can say which frames can change its book (`accept`):
 *   Holdings, for one, has nothing to read for an F&O fill. Analyzer updates
 *   that only report a read (an order status asked for, a book fetched) move
 *   nothing and are never answered.
 * - **Once more when the broker's book should have caught up.** Upstox's REST
 *   books can trail its own push by a second or two, so a read must start at
 *   least `CONFIRM_MS` after the last `order_update`. Any read that does
 *   counts; only when none has a moment later (`CONFIRM_SLACK_MS`) is one
 *   made for it. Fills a few seconds apart are thus confirmed by each other's
 *   reads, and a stream of them costs one confirm read at its end, not one
 *   per fill. A page opened, or come back to, just after a push asks once
 *   more the same way, so it never shows the book from before a fill.
 * - **When the Socket.IO connection comes back**, since anything pushed while
 *   it was down went to nobody. Not on the first connect: the page has just
 *   read its book.
 * - **On coming back to the page** after more than 15 seconds away, or after
 *   anything above asked while the page was hidden. A hidden page reads
 *   nothing: it only notes that its book has gone stale and reads once when
 *   it is seen again.
 * - **Every 45 seconds while a market is open** (or not known to be shut),
 *   so the figures never drift far from the broker's, events or not.
 *
 * One read at a time: anything that asks during a read queues a single read
 * after it, and a read that has not answered in `READ_TIMEOUT_MS` no longer
 * holds the next one up. A read the page starts itself (first load, Refresh,
 * a mode change) runs beside these, which is why every read is also guarded
 * by `useLatestRead`, so an answer that a newer read has overtaken is dropped.
 *
 * All of this is paid for out of one budget: OpenAlgo shares Upstox's standard
 * API limit among every page, tab and service, and once it is spent every call
 * waits, books included.
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

/** A read this long after the broker's last order update sees its book caught up. */
export const CONFIRM_MS = 2_500

/** How much longer a confirm read waits for another read to do its job. */
export const CONFIRM_SLACK_MS = 1_000

/** A read that has not answered by now no longer holds up the next one. */
export const READ_TIMEOUT_MS = 10_000

/** Analyzer updates that report a read, not a change: they move no book. */
const PASSIVE_ANALYZER = new Set([
  'orderstatus',
  'openposition',
  'orderbook',
  'tradebook',
  'positions',
  'positionbook',
  'holdings',
  'funds',
])

/** Whether an `analyzer_update` frame only reports a read. */
export function isPassiveAnalyzerUpdate(frame: unknown): boolean {
  const request = (frame as { request?: { api_type?: unknown } } | null)?.request
  const type = typeof request?.api_type === 'string' ? request.api_type.toLowerCase() : ''
  return PASSIVE_ANALYZER.has(type)
}

const EQUITY = new Set(['NSE', 'BSE'])

/**
 * Whether a frame can change the holdings: an equity delivery (CNC) order
 * filled, partly filled or rejected, or the sandbox engine's own update (its
 * fills and the T+1 move into holdings carry no request type). An F&O fill, a
 * cancel or a modify cannot.
 */
export function holdingsMayChange(event: OrderEventType, frame: unknown): boolean {
  const f = (frame ?? {}) as Record<string, unknown>
  const word = (value: unknown) => (value === null || value === undefined ? '' : String(value))
  if (event === 'order_update') {
    const product = word(f.product).trim().toUpperCase()
    const exchange = word(f.exchange).trim().toUpperCase()
    if (product && product !== 'CNC') return false
    if (exchange && !EQUITY.has(exchange)) return false
    const status = word(f.order_status).trim().toLowerCase()
    const filled = Number(f.filled_quantity) || 0
    return status === 'complete' || status === 'rejected' || filled > 0
  }
  if (event === 'analyzer_update') {
    const request = (f.request ?? {}) as Record<string, unknown>
    return word(request.api_type).trim() === ''
  }
  return false
}

export interface BookSyncOptions {
  enabled?: boolean
  events?: readonly OrderEventType[]
  /** How often to re-read while a market is open; null for no periodic re-read. */
  resyncMs?: number | null
  /**
   * How long away from the page makes coming back re-read; null for never,
   * for a page that reads on its own when it is shown again.
   */
  awayMs?: number | null
  /** Whether a market is open: false holds the periodic re-read, null (not known) does not. */
  marketOpen?: () => boolean | null
  /** Which frames can change this book; the rest are not answered. */
  accept?: (event: OrderEventType, frame: unknown) => boolean
  /** Told when coming back to the page starts a read, with that read. */
  onReturnRead?: (read: Promise<void>) => void
}

const NOT_KNOWN = () => null

function hidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

/** A read, or the timeout, whichever ends first. */
function bounded(work: unknown, ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms)
    Promise.resolve(work)
      .catch(() => {
        // The page's read reports its own failure; the next trigger tries again.
      })
      .finally(() => {
        clearTimeout(timer)
        resolve()
      })
  })
}

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
    accept,
    onReturnRead,
  } = options
  const { socket } = useSocketContext()

  const readRef = useRef(read)
  const marketOpenRef = useRef(marketOpen)
  const acceptRef = useRef(accept)
  const onReturnReadRef = useRef(onReturnRead)
  useEffect(() => {
    readRef.current = read
    marketOpenRef.current = marketOpen
    acceptRef.current = accept
    onReturnReadRef.current = onReturnRead
  }, [read, marketOpen, accept, onReturnRead])

  /** A read must start at or after this to confirm the last order update; null when none is owed. */
  const confirmAfter = useRef<number | null>(null)
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Something asked for a read while the page was hidden. */
  const missed = useRef(false)

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
        const owed = confirmAfter.current
        if (owed !== null && Date.now() >= owed) {
          confirmAfter.current = null
          if (confirmTimer.current) clearTimeout(confirmTimer.current)
          confirmTimer.current = null
        }
        let work: unknown
        try {
          work = readRef.current()
        } catch {
          work = undefined
        }
        await bounded(work, READ_TIMEOUT_MS)
      } while (queued.current)
    } finally {
      reading.current = false
    }
  }, [])

  /** A read now, or, while the page is hidden, one when it is seen again. */
  const ask = useCallback(() => {
    if (hidden()) {
      missed.current = true
      return
    }
    void sync()
  }, [sync])

  /** Owe a confirm read for an order update that arrived at `at`. */
  const oweConfirm = useCallback(
    (at: number, slack: number) => {
      const after = at + CONFIRM_MS
      if (confirmAfter.current === null || after > confirmAfter.current) {
        confirmAfter.current = after
      }
      if (confirmTimer.current) clearTimeout(confirmTimer.current)
      confirmTimer.current = setTimeout(
        () => {
          confirmTimer.current = null
          if (confirmAfter.current === null) return
          ask()
        },
        Math.max(0, (confirmAfter.current ?? after) + slack - Date.now())
      )
    },
    [ask]
  )

  /** A push this page would answer arrived a moment ago: confirm once the broker has caught up. */
  const answersUpdates = events.includes('order_update')
  const confirmRecentPush = useCallback(() => {
    const push = recentPush(CONFIRM_MS)
    if (!push || !answersUpdates) return
    if (acceptRef.current && !acceptRef.current('order_update', push.frame)) return
    oweConfirm(push.at, 0)
  }, [answersUpdates, oweConfirm])

  const eventsKey = events.join('|')

  // Order events, debounced into one read per burst, and the reconnect.
  // biome-ignore lint/correctness/useExhaustiveDependencies: events are keyed by content (eventsKey); callers pass inline arrays
  useEffect(() => {
    if (!enabled || !socket) return
    let settle: ReturnType<typeof setTimeout> | null = null
    let burstStartedAt = 0

    const answer = () => {
      settle = null
      ask()
    }
    const onEvent = () => {
      const now = Date.now()
      if (settle) clearTimeout(settle)
      else burstStartedAt = now
      const wait = Math.max(0, Math.min(SETTLE_MS, burstStartedAt + MAX_WAIT_MS - now))
      settle = setTimeout(answer, wait)
    }
    const listeners = [...events].map((event) => {
      const listener = (frame?: unknown) => {
        if (event === 'analyzer_update' && isPassiveAnalyzerUpdate(frame)) return
        if (acceptRef.current && !acceptRef.current(event, frame)) return
        if (hidden()) {
          missed.current = true
          return
        }
        onEvent()
        if (event === 'order_update') oweConfirm(Date.now(), CONFIRM_SLACK_MS)
      }
      socket.on(event, listener)
      return [event, listener] as const
    })

    // The first connect is not a reconnect: the page has just read its book.
    let connectedBefore = socket.connected === true
    const onConnect = () => {
      const count = connectionCount(socket)
      const again = count === undefined ? connectedBefore : count > 1
      connectedBefore = true
      if (again) ask()
    }
    socket.on('connect', onConnect)

    // A page opened just after a push: one more read when the broker has caught up.
    confirmRecentPush()

    return () => {
      for (const [event, listener] of listeners) socket.off(event, listener)
      socket.off('connect', onConnect)
      if (settle) clearTimeout(settle)
    }
  }, [socket, eventsKey, enabled, ask, oweConfirm, confirmRecentPush])

  // Coming back to the page: a read after a while away, or after anything
  // asked while it was hidden.
  useEffect(() => {
    if (!enabled || awayMs === null) return
    let hiddenAt: number | null = hidden() ? Date.now() : null
    const onVisibility = () => {
      if (hidden()) {
        hiddenAt ??= Date.now()
        return
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt
      hiddenAt = null
      if (!missed.current && away <= awayMs) return
      missed.current = false
      const done = sync()
      onReturnReadRef.current?.(done)
      confirmRecentPush()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [enabled, awayMs, sync, confirmRecentPush])

  // The slow re-sync while a market is open (or not known to be shut). A
  // hidden page skips it: coming back after a while reads anyway.
  useEffect(() => {
    if (!enabled || resyncMs === null) return
    const timer = setInterval(() => {
      if (hidden()) return
      if (marketOpenRef.current() === false) return
      void sync()
    }, resyncMs)
    return () => clearInterval(timer)
  }, [enabled, resyncMs, sync])

  // Nothing owed outlives the page.
  useEffect(
    () => () => {
      if (confirmTimer.current) clearTimeout(confirmTimer.current)
      confirmTimer.current = null
      confirmAfter.current = null
    },
    []
  )

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
