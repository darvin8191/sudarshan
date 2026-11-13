import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: sockets.current }),
}))

import { createQueryClient } from '@/app/providers'
import { EVENT_JOIN, EVENT_PUSH, type Push } from './types'
import { viewKey } from './useListView'
import { JOIN_WAIT_MS, SETTLE_MS, usePush } from './usePush'

type Handler = (...args: unknown[]) => void

function fakeSocket() {
  const handlers = new Map<string, Set<Handler>>()
  const socket = {
    connected: false,
    joins: [] as Array<(error: unknown, ack?: { ok?: boolean }) => void>,
    on: vi.fn((event: string, handler: Handler) => {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
    }),
    off: vi.fn((event: string, handler: Handler) => handlers.get(event)?.delete(handler)),
    timeout: vi.fn(() => ({
      emit: (event: string, ack: (error: unknown, ack?: { ok?: boolean }) => void) => {
        if (event === EVENT_JOIN) socket.joins.push(ack)
      },
    })),
    fire: (event: string, ...args: unknown[]) => {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    listeners: (event: string) => handlers.get(event)?.size ?? 0,
  }
  return socket
}

function setup(on = true) {
  const client = createQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  const socket = fakeSocket()
  sockets.current = socket
  const hook = renderHook(() => usePush(on), { wrapper })
  return { client, socket, ...hook }
}

const push = (v: string, ref = 'list:1'): Push => ({ v, ref, why: 'items' })

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('usePush', () => {
  it('does nothing while v2 is off: no listener, no join', () => {
    const { socket, result } = setup(false)
    expect(socket.on).not.toHaveBeenCalled()
    expect(socket.joins).toHaveLength(0)
    expect(result.current).toBe('unknown')
  })

  it('joins the owner room after a connect and says so on the ack', () => {
    const { socket, result } = setup()
    act(() => socket.fire('connect'))
    expect(socket.timeout).toHaveBeenCalledWith(JOIN_WAIT_MS)
    act(() => socket.joins[0](null, { ok: true }))
    expect(result.current).toBe('joined')
  })

  it('does not call a refused join joined (a session that is not the owner)', () => {
    const { socket, result } = setup()
    act(() => socket.fire('connect'))
    act(() => socket.joins[0](null, { ok: false }))
    expect(result.current).toBe('not_joined')
  })

  it('asks again when the join was not answered', () => {
    const { socket } = setup()
    act(() => socket.fire('connect'))
    act(() => socket.joins[0](new Error('timeout')))
    act(() => vi.advanceTimersByTime(2_000))
    expect(socket.joins).toHaveLength(2)
  })

  it('reads once for several pushes inside 200 ms, and not before', () => {
    const { client, socket } = setup()
    client.setQueryData(viewKey('list:1'), { version: '1' })
    const spy = vi.spyOn(client, 'invalidateQueries')
    act(() => socket.fire('connect'))
    act(() => {
      socket.fire(EVENT_PUSH, push('a:2'))
      vi.advanceTimersByTime(SETTLE_MS - 50)
      socket.fire(EVENT_PUSH, push('a:3'))
      vi.advanceTimersByTime(SETTLE_MS - 1)
    })
    expect(spy).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(1))
    expect(spy).toHaveBeenCalledTimes(3) // the view, the counts and OpenAlgo's lists, once each
  })

  it('ignores a push that is not shaped as one', () => {
    const { client, socket } = setup()
    const spy = vi.spyOn(client, 'invalidateQueries')
    act(() => {
      socket.fire(EVENT_PUSH, { nope: true })
      vi.advanceTimersByTime(SETTLE_MS)
    })
    expect(spy).not.toHaveBeenCalled()
  })

  it('reads everything again after a reconnect, not after the first connect', () => {
    const { client, socket } = setup()
    const spy = vi.spyOn(client, 'invalidateQueries')
    act(() => socket.fire('connect'))
    act(() => socket.joins[0](null, { ok: true }))
    expect(spy).not.toHaveBeenCalled()
    act(() => socket.fire('disconnect'))
    act(() => socket.fire('connect'))
    act(() => socket.joins[1](null, { ok: true }))
    expect(spy).toHaveBeenCalledTimes(2) // every v2 read, and OpenAlgo's lists
  })

  it('removes every listener and pending read when the page is left', () => {
    const { client, socket, unmount } = setup()
    const spy = vi.spyOn(client, 'invalidateQueries')
    act(() => socket.fire('connect'))
    act(() => socket.fire(EVENT_PUSH, push('a:2')))
    unmount()
    expect(
      socket.listeners('connect') + socket.listeners('disconnect') + socket.listeners(EVENT_PUSH)
    ).toBe(0)
    act(() => vi.advanceTimersByTime(5_000))
    expect(spy).not.toHaveBeenCalled()
  })
})
