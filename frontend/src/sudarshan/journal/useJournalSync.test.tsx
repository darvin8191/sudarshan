/**
 * When the journal's list is read again: 3 s after an order update (a burst is one read), on a
 * reconnect, on coming back after 15 s, every 45 s while shown and a market is not known shut, and
 * never while the page is hidden.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    connected: true,
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return this
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
      return this
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    count(event: string) {
      return handlers.get(event)?.size ?? 0
    },
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

import { fakeClock } from '@/test/frames'
import { AFTER_ORDER_MS, AWAY_MS, RESYNC_MS, useJournalSync } from './useJournalSync'

function visibility(state: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  fakeClock()
  socket.connected = true
})
afterEach(() => {
  visibility('visible')
  vi.useRealTimers()
})

const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })

describe('useJournalSync', () => {
  it('reads 3 s after an order update, and a burst of eight frames is one read', async () => {
    const read = vi.fn()
    renderHook(() => useJournalSync(read))
    for (let n = 0; n < 8; n += 1) {
      act(() => socket.fire('order_update', {}))
      await tick(250)
    }
    await tick(AFTER_ORDER_MS - 500)
    expect(read).not.toHaveBeenCalled()
    await tick(600)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('does not answer an analyzer update or anything but an order update', async () => {
    const read = vi.fn()
    renderHook(() => useJournalSync(read))
    act(() => socket.fire('analyzer_update', {}))
    await tick(AFTER_ORDER_MS + 100)
    expect(read).not.toHaveBeenCalled()
  })

  it('reads again when the connection comes back, not on the first connect', async () => {
    const read = vi.fn()
    socket.connected = false
    renderHook(() => useJournalSync(read))
    act(() => socket.fire('connect'))
    await tick(10)
    expect(read).not.toHaveBeenCalled()
    act(() => socket.fire('connect'))
    await tick(10)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('re-syncs every 45 s, unless the market is known shut', async () => {
    const read = vi.fn()
    let open: boolean | null = true
    renderHook(() => useJournalSync(read, { marketOpen: () => open }))
    await tick(RESYNC_MS + 10)
    expect(read).toHaveBeenCalledTimes(1)
    open = false
    await tick(RESYNC_MS)
    expect(read).toHaveBeenCalledTimes(1)
    open = null
    await tick(RESYNC_MS)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('reads nothing while hidden, and once on return after a while away', async () => {
    const read = vi.fn()
    renderHook(() => useJournalSync(read))
    act(() => visibility('hidden'))
    act(() => socket.fire('order_update', {}))
    await tick(RESYNC_MS * 2)
    expect(read).not.toHaveBeenCalled()
    act(() => visibility('visible'))
    await tick(10)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('a short look away reads nothing on return, a long one reads', async () => {
    const read = vi.fn()
    renderHook(() => useJournalSync(read))
    act(() => visibility('hidden'))
    await tick(AWAY_MS - 1000)
    act(() => visibility('visible'))
    await tick(10)
    expect(read).not.toHaveBeenCalled()
    act(() => visibility('hidden'))
    await tick(AWAY_MS + 1000)
    act(() => visibility('visible'))
    await tick(10)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('one read at a time: a trigger during a read queues a single read after it', async () => {
    let finish: () => void = () => {}
    const read = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    renderHook(() => useJournalSync(read))
    act(() => socket.fire('connect'))
    act(() => socket.fire('connect'))
    await tick(10)
    expect(read).toHaveBeenCalledTimes(1)
    act(() => socket.fire('connect'))
    act(() => socket.fire('connect'))
    await tick(10)
    expect(read).toHaveBeenCalledTimes(1)
    await act(async () => finish())
    await tick(10)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('lets go of the socket when the page goes', () => {
    const { unmount } = renderHook(() => useJournalSync(vi.fn()))
    expect(socket.count('order_update')).toBe(1)
    unmount()
    expect(socket.count('order_update')).toBe(0)
    expect(socket.count('connect')).toBe(0)
  })
})
