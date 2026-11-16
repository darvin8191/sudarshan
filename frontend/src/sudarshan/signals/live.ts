// SUDARSHAN M13.4 (P8 U1): TRINETRA's readings pushed to the owner's open screens (README section
// 9), on the app's one Socket.IO connection. After every connect the screen asks to join the
// owner's room (`sudarshan_signals_join`, answered `{ok}`); a session that is not the owner's is
// not joined and hears nothing. Each `sudarshan_signals_update` carries the `state` answer, or null
// when it would not fit (then the screen reads it). After a reconnect the screen reads the state
// again, since anything pushed meanwhile went to nobody.
//
// An older answer never replaces a newer one: every `state` answer, read or pushed, carries the
// server's `now_ms`, and the one shown is the newest of them. A read that comes back "switched
// off", or fails, after a push arrived while it was on its way, is dropped: the push is newer word
// that the service answers.
import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import type { Socket } from 'socket.io-client'
import { connectionCount } from '@/sudarshan/books/sharedSocket'
import { SETTINGS_KEY, STATE_KEY, TIMELINE_KEY } from './keys'
import { EVENT_UPDATE, JOIN_EVENT, type State } from './types'

/** What a screen shows: the state as of the server's `now_ms`, or the view switched off. */
export type SignalsView =
  | { kind: 'state'; state: State /** this phone's clock when it arrived */; at: number }
  | { kind: 'off'; message: string; at: number }

/** A server clock that went back further than this is taken as a step of the clock, not an old answer. */
export const CLOCK_STEP_MS = 10 * 60_000
/** How long a join waits for its answer; without one it is asked again. */
export const JOIN_WAIT_MS = 10_000
/** The waits before a join that was not answered is asked again; then the next connect asks. */
export const RETRY_MS = [2_000, 5_000, 10_000] as const

/** The `state` answer, when a value has its shape; null otherwise (it is read again, never shown). */
export function asState(value: unknown): State | null {
  if (!value || typeof value !== 'object') return null
  const state = value as Partial<State>
  if (typeof state.now_ms !== 'number' || !Number.isFinite(state.now_ms)) return null
  if (!state.freshness || typeof state.freshness !== 'object') return null
  if (!Array.isArray(state.readings)) return null
  return {
    ...(state as State),
    notes: Array.isArray(state.notes) ? state.notes : [],
    alerts: Array.isArray(state.alerts) ? state.alerts : [],
  }
}

/**
 * Whether `incoming` may replace `current`. A state replaces an older state (by the server's
 * clock) or a switched-off answer; a switched-off answer replaces anything unless a push arrived
 * after its read began.
 */
export function newer(
  current: SignalsView | undefined,
  incoming: SignalsView,
  pushedSince: boolean
): boolean {
  if (!current) return true
  if (incoming.kind === 'off') return !pushedSince
  if (current.kind !== 'state') return true
  const was = current.state.now_ms
  const now = incoming.state.now_ms
  return now >= was || was - now > CLOCK_STEP_MS
}

/** Pushes taken so far, so a read can tell whether one arrived while it was on its way. */
let pushes = 0
export function pushCount(): number {
  return pushes
}

/** What the timeline shows has moved: a reading began, changed, lapsed or went without data. */
function marks(state: State): string {
  return state.readings
    .map((each) =>
      [
        each.module,
        each.instrument,
        each.state,
        each.bias,
        each.lapsed,
        each.quality,
        each.since_ms,
      ].join('|')
    )
    .sort()
    .join('\n')
}

export function moved(before: SignalsView | undefined, next: State): boolean {
  return before?.kind !== 'state' || marks(before.state) !== marks(next)
}

/** One push, applied: the newest state shown, the timeline read again when it moved. */
export function applyPush(client: QueryClient, data: unknown): void {
  if (!data || typeof data !== 'object') return
  const { cause, state } = data as { cause?: unknown; state?: unknown }
  pushes += 1
  if (cause === 'settings') void client.invalidateQueries({ queryKey: SETTINGS_KEY })
  const next = state === null || state === undefined ? null : asState(state)
  if (next === null) {
    // Too large to push (or not readable): read it.
    void client.invalidateQueries({ queryKey: STATE_KEY })
    void client.invalidateQueries({ queryKey: TIMELINE_KEY })
    return
  }
  const before = client.getQueryData<SignalsView>(STATE_KEY)
  const incoming: SignalsView = { kind: 'state', state: next, at: Date.now() }
  if (!newer(before, incoming, false)) return
  client.setQueryData(STATE_KEY, incoming)
  if (cause === 'lapse' || moved(before, next)) {
    void client.invalidateQueries({ queryKey: TIMELINE_KEY })
  }
}

/** Whether this screen is in the owner's room: until it is, nothing says the readings arrive. */
export type Hearing = 'unknown' | 'joined' | 'not_joined'

let hearing: Hearing = 'unknown'
const listeners = new Set<() => void>()

function hear(next: Hearing): void {
  if (hearing === next) return
  hearing = next
  for (const listener of [...listeners]) listener()
}

export function useSignalsHearing(): Hearing {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    () => hearing,
    () => 'unknown'
  )
}

type Ack = (error: unknown, ack?: { ok?: unknown }) => void

/** Ask to join, with a time limit; a socket that cannot time its acks is given one here. */
function askToJoin(socket: Socket, done: Ack): void {
  if (typeof socket.timeout === 'function') {
    socket.timeout(JOIN_WAIT_MS).emit(JOIN_EVENT, done)
    return
  }
  let settled = false
  const timer = setTimeout(() => {
    if (settled) return
    settled = true
    done(new Error('no answer'))
  }, JOIN_WAIT_MS)
  socket.emit(JOIN_EVENT, (ack?: { ok?: unknown }) => {
    if (settled) return
    settled = true
    clearTimeout(timer)
    done(null, ack)
  })
}

function listen(client: QueryClient, socket: Socket): () => void {
  let alive = true
  const timers = new Set<ReturnType<typeof setTimeout>>()
  const current = (at: number) =>
    alive && socket.connected === true && (connectionCount(socket) ?? at) === at

  const later = (attempt: number, again: () => void) => {
    const wait = RETRY_MS[attempt]
    if (wait === undefined) return
    const timer = setTimeout(() => {
      timers.delete(timer)
      if (alive) again()
    }, wait)
    timers.add(timer)
  }
  const reread = () => {
    void client.invalidateQueries({ queryKey: STATE_KEY })
    void client.invalidateQueries({ queryKey: TIMELINE_KEY })
  }
  const join = (at: number, attempt: number, again: boolean) => {
    try {
      askToJoin(socket, (error, ack) => {
        if (!current(at)) return
        if (error || !ack || ack.ok !== true) {
          hear('not_joined')
          // Not in the room: read once all the same, so the screen is not left behind.
          if (again && attempt === 0) reread()
          later(attempt, () => current(at) && join(at, attempt + 1, false))
          return
        }
        hear('joined')
        if (again) reread()
      })
    } catch {
      hear('not_joined')
    }
  }
  // A connect does not change what is said: after a drop the screen already says it is out of the
  // room, and the first connect of a page says nothing until its join is answered.
  const onConnect = () => join(connectionCount(socket) ?? 0, 0, true)
  const onDisconnect = () => hear('not_joined')
  const onUpdate = (data: unknown) => applyPush(client, data)

  socket.on('connect', onConnect)
  socket.on('disconnect', onDisconnect)
  socket.on(EVENT_UPDATE, onUpdate)
  if (socket.connected === true) join(connectionCount(socket) ?? 0, 0, false)
  return () => {
    alive = false
    for (const timer of timers) clearTimeout(timer)
    socket.off('connect', onConnect)
    socket.off('disconnect', onDisconnect)
    socket.off(EVENT_UPDATE, onUpdate)
    hear('unknown')
  }
}

interface Attached {
  socket: Socket
  client: QueryClient
  holders: number
  stop: () => void
}

/** One set of listeners and one join per connection, however many screens hold it. */
let attached: Attached | null = null

/**
 * Hold the push on `socket` for this screen; returns the way to let go. The listeners go when the
 * last holder lets go and nothing has taken them again by the end of the task, so a screen
 * remounted in the same commit keeps the room.
 */
export function holdPush(client: QueryClient, socket: Socket): () => void {
  if (attached && (attached.socket !== socket || attached.client !== client)) {
    attached.stop()
    attached = null
  }
  if (!attached) attached = { socket, client, holders: 0, stop: listen(client, socket) }
  const mine = attached
  mine.holders += 1
  let released = false
  return () => {
    if (released) return
    released = true
    mine.holders = Math.max(0, mine.holders - 1)
    if (mine.holders > 0) return
    queueMicrotask(() => {
      if (attached !== mine || mine.holders > 0) return
      mine.stop()
      attached = null
    })
  }
}

/** Tests only: forget the pushes counted, the room and the listeners. */
export function resetSignalsLiveForTests(): void {
  attached?.stop()
  attached = null
  pushes = 0
  hearing = 'unknown'
}
