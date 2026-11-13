import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { Socket } from 'socket.io-client'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { WATCHLISTS_KEY } from '../useWatchlists'
import { syncListAlerts } from './alertSync'
import { afterWrites } from './ownWrites'
import { EVENT_JOIN, EVENT_PUSH, type Push } from './types'
import { applyPushes, V2_KEY } from './useListView'

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
    // `refs` is absent from an older server: refsOf falls back to `ref` then.
    (push.refs === undefined || Array.isArray(push.refs)) &&
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
    const release = new Set<() => void>()

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
          if (ack?.ok === true && mine > 1) {
            void client.invalidateQueries({ queryKey: V2_KEY })
            void client.invalidateQueries({ queryKey: WATCHLISTS_KEY, exact: true })
          }
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
        // M8.14 (review M5): a list alert on a ref the batch names follows it now, own writes included.
        syncListAlerts(client, batch)
        // A write of this page still in flight: wait for its answer, so its own push is known.
        let ran = false
        let cancel: (() => void) | undefined
        cancel = afterWrites(() => {
          ran = true
          if (cancel) release.delete(cancel)
          applyPushes(client, batch)
        })
        if (!ran) release.add(cancel)
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
      for (const cancel of release) cancel()
    }
  }, [on, socket, client])

  return on ? hearing : 'unknown'
}
