import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})
const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: sockets.current }),
}))

import { createQueryClient } from '@/app/providers'
import { callOp } from './api'
import { HOLD_MS, KEEP_MS, resetOwnWrites, takeOwn } from './ownWrites'
import { EVENT_PUSH, type Push } from './types'
import { SETTLE_MS, usePush } from './usePush'

const b = () => bridge.current as NonNullable<typeof bridge.current>

type Handler = (...args: unknown[]) => void
function fakeSocket() {
  const handlers = new Map<string, Set<Handler>>()
  return {
    connected: false,
    on: (event: string, handler: Handler) =>
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler)),
    off: (event: string, handler: Handler) => handlers.get(event)?.delete(handler),
    timeout: () => ({ emit: () => {} }),
    fire: (event: string, ...args: unknown[]) => {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
  }
}

function setup() {
  const client = createQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  const socket = fakeSocket()
  sockets.current = socket
  renderHook(() => usePush(true), { wrapper })
  return { client, socket, spy: vi.spyOn(client, 'invalidateQueries') }
}

const push = (v: string, refs: string[] = ['list:1']): Push => ({
  v,
  ref: refs[0] ?? 'lists',
  refs,
  why: 'items',
})
const settle = () => act(() => vi.advanceTimersByTimeAsync(SETTLE_MS))

beforeEach(() => {
  vi.useFakeTimers()
  b().reset()
  resetOwnWrites()
})
afterEach(() => vi.useRealTimers())

describe('a push names every list it changed (refs)', () => {
  it('refreshes exactly those lists: two refs for a move, none for an empty refs', async () => {
    const { socket, spy } = setup()
    socket.fire(EVENT_PUSH, push('a:2', ['list:1', 'list:2']))
    await settle()
    const keys = spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey))
    expect(keys.filter((k) => k.includes('"view"'))).toHaveLength(2)
    expect(keys.some((k) => k.includes('list:3'))).toBe(false)
    spy.mockClear()
    socket.fire(EVENT_PUSH, { v: 'a:3', ref: 'lists', refs: [], why: 'flags' })
    socket.fire(EVENT_PUSH, push('a:4', []))
    await settle()
    const empty = spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey))
    expect(empty.filter((k) => k.includes('list:'))).toEqual([])
  })

  it('reads the one ref when an older server sends no refs', async () => {
    const { socket, spy } = setup()
    socket.fire(EVENT_PUSH, { v: 'a:2', ref: 'list:9', why: 'items' })
    await settle()
    expect(spy.mock.calls.some(([f]) => JSON.stringify(f?.queryKey).includes('list:9'))).toBe(true)
  })
})

describe('the push of this page own write (lists_version)', () => {
  const write = () => {
    b().on('copy', () => ({
      list: 1,
      added: 1,
      skipped: 0,
      version: '5',
      words: null,
      lists_version: 'a:7',
    }))
    return callOp('copy', { source: { ref: 'list:1' }, target: { list: 2 } } as never)
  }

  it('reads nothing for the push that comes after the answer', async () => {
    const { socket, spy } = setup()
    await write()
    socket.fire(EVENT_PUSH, push('a:7', ['list:2']))
    await settle()
    expect(spy).not.toHaveBeenCalled()
  })

  it('reads nothing for the push that comes before the answer, and the answer applies', async () => {
    const { socket, spy } = setup()
    let answer: (v: unknown) => void = () => {}
    b().on(
      'copy',
      () =>
        new Promise((r) => {
          answer = r
        })
    )
    const pending = callOp('copy', { source: { ref: 'list:1' }, target: { list: 2 } } as never)
    await act(() => Promise.resolve())
    socket.fire(EVENT_PUSH, push('a:7', ['list:2']))
    await settle()
    expect(spy).not.toHaveBeenCalled() // held for the write in flight
    answer({ list: 2, added: 1, skipped: 0, version: '5', words: null, lists_version: 'a:7' })
    const got = await pending
    expect(got.added).toBe(1)
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(spy).not.toHaveBeenCalled()
  })

  it('still reads a push from another screen, even beside a write of its own', async () => {
    const { socket, spy } = setup()
    await write()
    socket.fire(EVENT_PUSH, push('a:8', ['list:2']))
    await settle()
    expect(spy).toHaveBeenCalled()
  })

  it('a held push is read when the write never answers in time', async () => {
    const { socket, spy } = setup()
    b().on('copy', () => new Promise(() => {}))
    void callOp('copy', { source: { ref: 'list:1' }, target: { list: 2 } } as never)
    await act(() => Promise.resolve())
    socket.fire(EVENT_PUSH, push('a:9', ['list:2']))
    await act(() => vi.advanceTimersByTimeAsync(SETTLE_MS + HOLD_MS))
    expect(spy).toHaveBeenCalled()
  })

  it('a write whose answer has no lists_version changes nothing', async () => {
    const { socket, spy } = setup()
    b().on('copy', () => ({ list: 1, added: 0, skipped: 1, version: '5', words: null }))
    await callOp('copy', { source: { ref: 'list:1' }, target: { list: 2 } } as never)
    socket.fire(EVENT_PUSH, push('a:7', ['list:2']))
    await settle()
    expect(spy).toHaveBeenCalled()
  })

  it('drops a version whose push never came, after a short bound', async () => {
    await write()
    await act(() => vi.advanceTimersByTimeAsync(KEEP_MS + 1))
    expect(takeOwn('a:7')).toBe(false)
  })
})
