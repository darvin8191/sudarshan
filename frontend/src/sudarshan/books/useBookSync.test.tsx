/**
 * When the books are read again (M6.1 to M6.4): after every order event
 * including the broker's own fills (`order_update`), one read per burst, once
 * more when the broker may have trailed its own push, on a Socket.IO
 * reconnect, on coming back after 15 seconds, and every 45 seconds only while
 * a market is open. And a late answer never replaces a newer one.
 */
import { act, render, renderHook, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
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
    listeners: (event: string) => handlers.get(event)?.size ?? 0,
    clear: () => handlers.clear(),
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { CONFIRM_MS, MAX_WAIT_MS, SETTLE_MS, useBookSync, useLatestRead } from './useBookSync'

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  socket.clear()
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('useBookSync', () => {
  it("reads after the broker's order update, and once more in case its book trailed the push", async () => {
    const read = vi.fn()
    renderHook(() => useBookSync(read))
    expect(socket.listeners('order_update')).toBe(1)

    act(() => socket.fire('order_update', { orderid: '1', order_status: 'complete' }))
    await pass(SETTLE_MS)
    expect(read).toHaveBeenCalledTimes(1)

    await pass(CONFIRM_MS)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('reads after the events the page already listened to', async () => {
    const read = vi.fn()
    renderHook(() => useBookSync(read))
    for (const event of ['order_event', 'analyzer_update', 'close_position_event']) {
      act(() => socket.fire(event, {}))
      await pass(SETTLE_MS)
    }
    expect(read).toHaveBeenCalledTimes(3)
  })

  it('answers a burst with one read, never later than a second after it began', async () => {
    const read = vi.fn()
    renderHook(() => useBookSync(read, { events: ['order_event'] }))

    // Five events 300 ms apart: each restarts the quiet wait, but the burst
    // is answered a second after its first event all the same.
    for (let i = 0; i < 5; i++) {
      act(() => socket.fire('order_event', {}))
      await pass(300)
    }
    expect(read).toHaveBeenCalledTimes(1)
    await pass(MAX_WAIT_MS)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('reads one at a time: what asks during a read becomes one read after it', async () => {
    let finish: () => void = () => {}
    const read = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    const { result } = renderHook(() => useBookSync(read, { events: ['order_event'] }))

    void result.current()
    expect(read).toHaveBeenCalledTimes(1)
    void result.current()
    void result.current()
    expect(read).toHaveBeenCalledTimes(1)

    await act(async () => finish())
    expect(read).toHaveBeenCalledTimes(2)
    await act(async () => finish())
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('reads when the Socket.IO connection comes back', async () => {
    const read = vi.fn()
    renderHook(() => useBookSync(read))

    act(() => socket.fire('connect'))
    await pass(0)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('reads on coming back after more than 15 seconds, not after a glance away', async () => {
    const read = vi.fn()
    renderHook(() => useBookSync(read, { resyncMs: null }))

    act(() => setVisibility('hidden'))
    await pass(5_000)
    act(() => setVisibility('visible'))
    expect(read).not.toHaveBeenCalled()

    act(() => setVisibility('hidden'))
    await pass(16_000)
    act(() => setVisibility('visible'))
    await pass(0)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('re-syncs every 45 seconds while a market is open or not known to be shut, never while all are', async () => {
    const read = vi.fn()
    let open: boolean | null = false
    renderHook(() => useBookSync(read, { marketOpen: () => open }))

    await pass(RESYNC_MS * 3)
    expect(read).not.toHaveBeenCalled()

    open = true
    await pass(RESYNC_MS)
    expect(read).toHaveBeenCalledTimes(1)

    open = null
    await pass(RESYNC_MS)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('stops listening when the page goes', async () => {
    const read = vi.fn()
    const { unmount } = renderHook(() => useBookSync(read))
    unmount()
    expect(socket.listeners('order_update')).toBe(0)
    expect(socket.listeners('connect')).toBe(0)
    act(() => socket.fire('order_update', {}))
    await pass(RESYNC_MS + CONFIRM_MS)
    expect(read).not.toHaveBeenCalled()
  })
})

describe('useLatestRead', () => {
  it('drops an answer a newer read has overtaken', async () => {
    const answers: Array<(value: string) => void> = []
    function Book() {
      const begin = useLatestRead()
      const [shown, setShown] = useState('none')
      const [reads, setReads] = useState(0)
      useEffect(() => {
        if (reads === 0) return
        const isLatest = begin()
        new Promise<string>((resolve) => answers.push(resolve)).then((value) => {
          if (isLatest()) setShown(value)
        })
      }, [reads, begin])
      return (
        <>
          <button type="button" onClick={() => setReads((n) => n + 1)}>
            read
          </button>
          <output>{shown}</output>
        </>
      )
    }
    render(<Book />)
    act(() => screen.getByRole('button').click())
    act(() => screen.getByRole('button').click())

    await act(async () => answers[1]('newer'))
    await act(async () => answers[0]('older, late'))
    expect(screen.getByRole('status')).toHaveTextContent('newer')
  })
})
