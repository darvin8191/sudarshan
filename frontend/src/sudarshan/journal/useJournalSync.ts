import { useEffect, useRef } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { RESYNC_MS } from '@/sudarshan/dashboard/live'

/**
 * When the journal's list is read again: a re-sync of what the service holds, never market data
 * (rule 7: figures between reads move only with ticks). The service records a fill a moment after
 * Upstox reports it, so a read comes `AFTER_ORDER_MS` after the last `order_update` of a burst
 * (the owner's phone shows the trade a few seconds after the fill, design 6.2); on a reconnect of
 * the Socket.IO connection (anything pushed while it was down went to nobody); on coming back to
 * the page after more than `AWAY_MS`; and every `RESYNC_MS` while the page is shown and a market
 * is open or not known to be shut. A hidden page reads nothing: it reads once when it is seen
 * again. One read at a time; a trigger during a read queues one read after it.
 */
export const AFTER_ORDER_MS = 3_000
export const AWAY_MS = 15_000
export { RESYNC_MS }

const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'

export function useJournalSync(
  read: () => unknown,
  {
    enabled = true,
    marketOpen = () => null,
  }: { enabled?: boolean; marketOpen?: () => boolean | null } = {}
): void {
  const { socket } = useSocketContext()
  const readRef = useRef(read)
  const marketRef = useRef(marketOpen)
  useEffect(() => {
    readRef.current = read
    marketRef.current = marketOpen
  })
  const reading = useRef(false)
  const queued = useRef(false)
  const missed = useRef(false)

  useEffect(() => {
    if (!enabled) return
    const run = async () => {
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
            // The page shows its own failure; the next trigger tries again.
          }
        } while (queued.current)
      } finally {
        reading.current = false
      }
    }
    const ask = () => {
      if (hidden()) {
        missed.current = true
        return
      }
      void run()
    }

    let settle: ReturnType<typeof setTimeout> | null = null
    const onOrder = () => {
      if (hidden()) {
        missed.current = true
        return
      }
      if (settle) clearTimeout(settle)
      settle = setTimeout(() => {
        settle = null
        ask()
      }, AFTER_ORDER_MS)
    }
    let connectedBefore = socket?.connected === true
    const onConnect = () => {
      const again = connectedBefore
      connectedBefore = true
      if (again) ask()
    }
    socket?.on('order_update', onOrder)
    socket?.on('connect', onConnect)

    let hiddenAt: number | null = hidden() ? Date.now() : null
    const onVisibility = () => {
      if (hidden()) {
        hiddenAt ??= Date.now()
        return
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt
      hiddenAt = null
      if (!missed.current && away <= AWAY_MS) return
      missed.current = false
      void run()
    }
    document.addEventListener('visibilitychange', onVisibility)

    const timer = setInterval(() => {
      if (hidden() || marketRef.current() === false) return
      void run()
    }, RESYNC_MS)

    return () => {
      socket?.off('order_update', onOrder)
      socket?.off('connect', onConnect)
      document.removeEventListener('visibilitychange', onVisibility)
      clearInterval(timer)
      if (settle) clearTimeout(settle)
    }
  }, [enabled, socket])
}
