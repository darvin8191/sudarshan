import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { Socket } from 'socket.io-client'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { applyPushes, V2_KEY } from './useListView'
import { EVENT_JOIN, EVENT_PUSH, type Push } from './types'

/** Pushes this close together make one read. */
export const SETTLE_MS = 200
/** How long a join waits for its ack. */
export const JOIN_WAIT_MS = 10_000
/** How long a join without an ack waits before it is asked again. */
export const RETRY_MS = [2_000, 5_000, 10_000] as const

/** Whether this screen hears the owner's room: `joined` from the ack until the connection drops. */
export type Hearing = 'unknown' | 'joined' | 'not_joined'

function isPush(value: unknown): value is Push {
  const push = value as Partial<Push> | null
  return (
    push !== null &&
    typeof push === 'object' &&
    typeof push.v === 'string' &&
    typeof push.ref === 'string' &&
    typeof push.why === 'string'
  )
}

/**
 * The change push (design 2.5): the server says a list, a section or a flag list changed on any
 * device, and the open page reads what that names, once, 200 ms after the last push. After every
 * connect it joins the owner's room with an ack; a reconnect reads everything again, because
 * whatever was pushed while the connection was down went to nobody. Nothing is read on a timer.
 *
 * `on` is false while v2 is switched off: nothing joins and nothing listens.
 */
export function usePush(on: boolean): Hearing {
  const { socket } = useSocketContext()
  const client = useQueryClient()
  const [hearing, setHearing] = useState<Hearing>('unknown')

  useEffect(() => {
    if (!on || !socket) {
      setHearing('unknown')
      return
    }
    const timers = new Set<ReturnType<typeof setTimeout>>()
    let pending: Push[] = []
    let settle: ReturnType<typeof setTimeout> | null = null
    let connection = 0
    let live = true

    const join = (mine: number, attempt: number) => {
      ;(socket as Socket)
        .timeout(JOIN_WAIT_MS)
        .emit(EVENT_JOIN, (error: unknown, ack?: { ok?: unknown }) => {
          if (!live || mine !== connection) return
          if (error) {
            const wait = RETRY_MS[attempt]
            if (wait === undefined) return
            const timer = setTimeout(() => {
              timers.delete(timer)
              if (live && mine === connection) join(mine, attempt + 1)
            }, wait)
            timers.add(timer)
            return
          }
          setHearing(ack?.ok === true ? 'joined' : 'not_joined')
          // A reconnect: what was pushed while the connection was down went to nobody.
          if (ack?.ok === true && mine > 1) void client.invalidateQueries({ queryKey: V2_KEY })
        })
    }

    const onConnect = () => {
      connection += 1
      join(connection, 0)
    }
    const onDisconnect = () => setHearing('unknown')
    const onPush = (value: unknown) => {
      if (!isPush(value)) return
      pending.push(value)
      if (settle) clearTimeout(settle)
      settle = setTimeout(() => {
        settle = null
        const batch = pending
        pending = []
        applyPushes(client, batch)
      }, SETTLE_MS)
    }

    socket.on('connect', onConnect)
    socket.on('disconnect', onDisconnect)
    socket.on(EVENT_PUSH, onPush)
    if (socket.connected) onConnect()
    return () => {
      live = false
      socket.off('connect', onConnect)
      socket.off('disconnect', onDisconnect)
      socket.off(EVENT_PUSH, onPush)
      if (settle) clearTimeout(settle)
      for (const timer of timers) clearTimeout(timer)
    }
  }, [on, socket, client])

  return on ? hearing : 'unknown'
}
