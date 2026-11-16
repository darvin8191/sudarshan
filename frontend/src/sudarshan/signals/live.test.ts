/**
 * The TRINETRA push (U1, README section 9) on the app's shared connection: the join after every
 * connect with its ack, the state read again after a reconnect, one set of listeners however many
 * screens hold it, and an older answer never replacing a newer one. The pushed state is the
 * recorded `state` answer of the real service.
 */
import { QueryClient } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { Socket } from 'socket.io-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stateOf, viewOf } from '@/test/signalsAnswers'
import { SETTINGS_KEY, STATE_KEY, TIMELINE_KEY } from './keys'
import {
  applyPush,
  asState,
  CLOCK_STEP_MS,
  holdPush,
  JOIN_WAIT_MS,
  moved,
  newer,
  pushCount,
  RETRY_MS,
  resetSignalsLiveForTests,
  type SignalsView,
  useSignalsHearing,
} from './live'
import { EVENT_UPDATE, JOIN_EVENT, type State } from './types'

type Ack = (error: unknown, ack?: { ok?: unknown }) => void

/** The app's connection, reduced to what the push uses; `timeout` as Socket.IO's. */
function fakeSocket({ timed = true }: { timed?: boolean } = {}) {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  const joins: Ack[] = []
  const socket = {
    connected: false,
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return socket
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
      return socket
    },
    emit(event: string, ack: (answer?: { ok?: unknown }) => void) {
      if (event === JOIN_EVENT) joins.push((_error, answer) => ack(answer))
      return socket
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    listeners: (event: string) => handlers.get(event)?.size ?? 0,
  } as Record<string, unknown>
  if (timed) {
    socket.timeout = (ms: number) => {
      expect(ms).toBe(JOIN_WAIT_MS)
      return {
        emit: (event: string, ack: Ack) => {
          if (event === JOIN_EVENT) joins.push(ack)
        },
      }
    }
  }
  return {
    socket: socket as unknown as Socket & {
      connected: boolean
      fire: (event: string, ...args: unknown[]) => void
      listeners: (event: string) => number
    },
    joins,
  }
}

function at(state: State, nowMs: number): State {
  return { ...state, now_ms: nowMs }
}

let client: QueryClient

beforeEach(() => {
  resetSignalsLiveForTests()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(() => {
  resetSignalsLiveForTests()
  vi.useRealTimers()
})

describe('an older answer never replaces a newer one', () => {
  const state = stateOf()
  const off: SignalsView = { kind: 'off', message: 'The TRINETRA view is switched off.', at: 0 }

  it('by the server clock of the two states', () => {
    const shown = viewOf(state)
    expect(newer(undefined, shown, false)).toBe(true)
    expect(newer(shown, viewOf(at(state, state.now_ms + 1)), false)).toBe(true)
    expect(newer(shown, viewOf(at(state, state.now_ms)), false)).toBe(true)
    expect(newer(shown, viewOf(at(state, state.now_ms - 1)), false)).toBe(false)
  })

  it('a server clock stepped back by more than ten minutes is a step, not an old answer', () => {
    const shown = viewOf(state)
    expect(newer(shown, viewOf(at(state, state.now_ms - CLOCK_STEP_MS)), false)).toBe(false)
    expect(newer(shown, viewOf(at(state, state.now_ms - CLOCK_STEP_MS - 1)), false)).toBe(true)
  })

  it('"switched off" replaces a state unless a push came while its read was on its way', () => {
    expect(newer(viewOf(state), off, false)).toBe(true)
    expect(newer(viewOf(state), off, true)).toBe(false)
    expect(newer(off, viewOf(state), false)).toBe(true)
  })

  it("a value without the state answer's shape is never shown", () => {
    expect(asState(null)).toBeNull()
    expect(asState({ now_ms: 'soon', freshness: {}, readings: [] })).toBeNull()
    expect(asState({ now_ms: 1, readings: [] })).toBeNull()
    expect(asState({ now_ms: 1, freshness: {}, readings: [] })).toMatchObject({
      notes: [],
      alerts: [],
    })
  })
})

describe('a push, applied', () => {
  const state = stateOf()

  it('shows the pushed state and reads the timeline again when a reading moved', () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const before = pushCount()
    applyPush(client, { cause: 'document', state })
    expect(client.getQueryData<SignalsView>(STATE_KEY)).toMatchObject({ kind: 'state', state })
    expect(pushCount()).toBe(before + 1)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: TIMELINE_KEY })
  })

  it('a move of the confidence alone does not read the timeline again', () => {
    client.setQueryData(STATE_KEY, viewOf(state))
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const nudged = structuredClone(state)
    nudged.now_ms += 1000
    nudged.readings[0].confidence = 0.73
    applyPush(client, { cause: 'document', state: nudged })
    expect(client.getQueryData<SignalsView>(STATE_KEY)).toMatchObject({ state: nudged })
    expect(invalidate).not.toHaveBeenCalled()
    expect(moved(viewOf(state), nudged)).toBe(false)
  })

  it('an older push is dropped', () => {
    client.setQueryData(STATE_KEY, viewOf(state))
    applyPush(client, { cause: 'document', state: at(state, state.now_ms - 5_000) })
    expect(client.getQueryData<SignalsView>(STATE_KEY)).toMatchObject({ state })
  })

  it('a state too large to push (null) is read; a lapse reads the timeline; settings, the settings', () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    applyPush(client, { cause: 'document', state: null })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: STATE_KEY })
    invalidate.mockClear()
    client.setQueryData(STATE_KEY, viewOf(state))
    applyPush(client, { cause: 'lapse', state: at(state, state.now_ms + 1) })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: TIMELINE_KEY })
    invalidate.mockClear()
    applyPush(client, { cause: 'settings', state: at(state, state.now_ms + 2) })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: SETTINGS_KEY })
  })

  it('something that is not an update changes nothing and throws nothing', () => {
    applyPush(client, 'hello')
    applyPush(client, null)
    expect(client.getQueryData(STATE_KEY)).toBeUndefined()
  })
})

describe('the owner room on the shared connection', () => {
  it('joins at once on a connected socket, and does not read again for that first join', () => {
    const { socket, joins } = fakeSocket()
    socket.connected = true
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => useSignalsHearing())
    let release = () => {}
    act(() => {
      release = holdPush(client, socket)
    })
    expect(joins).toHaveLength(1)
    expect(result.current).toBe('unknown')
    act(() => joins[0](null, { ok: true }))
    expect(result.current).toBe('joined')
    expect(invalidate).not.toHaveBeenCalled()
    act(() => release())
  })

  it('after a reconnect: not in the room until the new join is answered, then the state is read', () => {
    const { socket, joins } = fakeSocket()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => useSignalsHearing())
    act(() => {
      holdPush(client, socket)
    })
    // Not connected yet: nothing said until a join is answered (no "Reconnecting" at first load).
    expect(result.current).toBe('unknown')
    expect(joins).toHaveLength(0)
    socket.connected = true
    act(() => socket.fire('connect'))
    expect(joins).toHaveLength(1)
    expect(result.current).toBe('unknown')
    act(() => joins[0](null, { ok: true }))
    expect(result.current).toBe('joined')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: STATE_KEY })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: TIMELINE_KEY })
    socket.connected = false
    act(() => socket.fire('disconnect', 'transport close'))
    expect(result.current).toBe('not_joined')
    // Back: still out of the room until the new join is answered.
    socket.connected = true
    act(() => socket.fire('connect'))
    expect(result.current).toBe('not_joined')
    act(() => joins[1](null, { ok: true }))
    expect(result.current).toBe('joined')
  })

  it('a refused join (not the owner) hears nothing, reads once, and asks again 2, 5 and 10 s later', () => {
    vi.useFakeTimers()
    const { socket, joins } = fakeSocket()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => useSignalsHearing())
    act(() => {
      holdPush(client, socket)
    })
    socket.connected = true
    act(() => socket.fire('connect'))
    act(() =>
      joins[0](null, { ok: false, message: 'Only the account owner can use the TRINETRA view.' })
    )
    expect(result.current).toBe('not_joined')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: STATE_KEY })
    for (const [n, wait] of RETRY_MS.entries()) {
      act(() => {
        vi.advanceTimersByTime(wait)
      })
      expect(joins).toHaveLength(n + 2)
      act(() => joins[n + 1](new Error('timeout')))
    }
    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(joins).toHaveLength(1 + RETRY_MS.length) // then the next connect asks
  })

  it('takes the pushes it hears', () => {
    const { socket } = fakeSocket()
    act(() => {
      holdPush(client, socket)
    })
    const state = stateOf()
    act(() => socket.fire(EVENT_UPDATE, { cause: 'document', state }))
    expect(client.getQueryData<SignalsView>(STATE_KEY)).toMatchObject({ state })
  })

  it('one set of listeners and one join for every screen holding it; gone with the last', async () => {
    const { socket, joins } = fakeSocket()
    socket.connected = true
    const first = holdPush(client, socket)
    const second = holdPush(client, socket)
    expect(socket.listeners(EVENT_UPDATE)).toBe(1)
    expect(joins).toHaveLength(1)
    first()
    first() // letting go twice counts once
    await Promise.resolve()
    expect(socket.listeners(EVENT_UPDATE)).toBe(1)
    second()
    await Promise.resolve()
    expect(socket.listeners(EVENT_UPDATE)).toBe(0)
    expect(socket.listeners('connect')).toBe(0)
  })

  it('a screen remounted in the same task keeps the room', async () => {
    const { socket, joins } = fakeSocket()
    socket.connected = true
    const before = holdPush(client, socket)
    before()
    const after = holdPush(client, socket)
    await Promise.resolve()
    expect(socket.listeners(EVENT_UPDATE)).toBe(1)
    expect(joins).toHaveLength(1)
    after()
  })

  it('a socket that cannot time its acks is given a time limit here', () => {
    vi.useFakeTimers()
    const { socket, joins } = fakeSocket({ timed: false })
    socket.connected = true
    const { result } = renderHook(() => useSignalsHearing())
    act(() => {
      holdPush(client, socket)
    })
    expect(joins).toHaveLength(1)
    act(() => {
      vi.advanceTimersByTime(JOIN_WAIT_MS)
    })
    expect(result.current).toBe('not_joined')
  })
})
