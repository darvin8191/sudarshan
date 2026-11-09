import { useEffect, useReducer, useState } from 'react'
import type { Socket } from 'socket.io-client'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'

/** How long a page's first connect may take before the line says "Connecting". */
export const FIRST_CONNECT_GRACE_MS = 3_000

/** Down this long, the line takes the attention colour. */
export const STALE_AFTER_MS = 20_000

/** Where the tab's Socket.IO connection stands, and since when it is down. */
interface Standing {
  connected: boolean
  everConnected: boolean
  downSince: number | null
  openedAt: number
}

/**
 * Whether the tab's connection to the server is up. Order events, fills and rejections, Exit all's
 * result, market alerts and the kill switch all arrive on it, so while it is down the screens'
 * order figures are as old as the drop (rule 7). Ticks one second at a time while it is down, for
 * the age; nothing while it is up.
 */
export function useUpdatesStanding(socket: Socket | null): Standing & { now: number } {
  const [standing, setStanding] = useState<Standing>(() => ({
    connected: socket?.connected === true,
    everConnected: socket?.connected === true,
    downSince: null,
    openedAt: Date.now(),
  }))
  const [now, tick] = useReducer(() => Date.now(), Date.now())

  useEffect(() => {
    if (!socket) return
    setStanding((was) =>
      socket.connected
        ? { ...was, connected: true, everConnected: true, downSince: null }
        : { ...was, connected: false }
    )
    const up = () =>
      setStanding((was) => ({ ...was, connected: true, everConnected: true, downSince: null }))
    const down = () =>
      setStanding((was) => ({
        ...was,
        connected: false,
        downSince: was.downSince ?? (was.everConnected ? Date.now() : null),
      }))
    socket.on('connect', up)
    socket.on('disconnect', down)
    return () => {
      socket.off('connect', up)
      socket.off('disconnect', down)
    }
  }, [socket])

  const down = socket !== null && !standing.connected
  useEffect(() => {
    if (!down) return
    tick()
    const timer = setInterval(tick, 1_000)
    return () => clearInterval(timer)
  }, [down])

  return { ...standing, now }
}

/**
 * The line every page shows while the tab's connection is down: "Order updates", then the app's
 * own "Reconnecting · updated 12s ago" (or "Connecting" on a first connect that has not come
 * within a few seconds), in words and an icon, never a colour alone. Nothing while it is up.
 */
export function UpdatesLine({ socket }: { socket: Socket | null }) {
  const { connected, everConnected, downSince, openedAt, now } = useUpdatesStanding(socket)
  if (socket === null || connected) return null
  if (!everConnected && now - openedAt < FIRST_CONNECT_GRACE_MS) return null
  const stale = downSince !== null && now - downSince > STALE_AFTER_MS
  return (
    <div
      data-testid="updates-banner"
      className="flex items-center justify-center gap-2 border-b border-[color:var(--divider)] bg-[color:var(--surface-card)] px-4 py-1.5"
    >
      <span className="text-xs font-semibold text-[color:var(--text-secondary)]">
        Order updates
      </span>
      <LiveLine
        line={{ kind: 'connecting', at: downSince, stale, again: everConnected }}
        now={now}
      />
    </div>
  )
}

/** UpdatesLine on the tab's shared connection, in both shells beside the Analyze banner. */
export function UpdatesBanner() {
  const { socket } = useSocketContext()
  return <UpdatesLine socket={socket} />
}
