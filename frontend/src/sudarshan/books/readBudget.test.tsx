/**
 * What the books cost in broker calls while orders fill. OpenAlgo pools every
 * standard Upstox call (books, quotes, funds) into one budget, 475 a minute
 * and 1,900 in 30 minutes, shared by every page, tab and service; once it is
 * spent every call waits, and the books freeze while the screen still says
 * "Real-time". This replays the review's worst cases against the pages that
 * read a book, each as it is wired: Positions, Order book, Trade book,
 * Holdings (which answers only an update that can change it), the dashboard
 * (the position book and the funds) and the scalping terminal (positions,
 * orders and trades).
 *
 * One fill is the app's order event and five broker updates over 400 ms.
 */
import { act, renderHook } from '@testing-library/react'
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
    clear: () => handlers.clear(),
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

import { resetOrderMemoryForTests } from './orderFold'
import { type BookSyncOptions, holdingsMayChange, useBookSync } from './useBookSync'

interface Page {
  name: string
  /** Broker calls one read of this page makes. */
  calls: number
  options?: BookSyncOptions
  reads: number
}

function pages(): Page[] {
  return [
    { name: 'positions', calls: 1, reads: 0 },
    { name: 'orders', calls: 1, reads: 0 },
    { name: 'trades', calls: 1, reads: 0 },
    { name: 'holdings', calls: 1, options: { accept: holdingsMayChange }, reads: 0 },
    { name: 'dashboard', calls: 2, reads: 0 },
    { name: 'scalping', calls: 3, options: { awayMs: null }, reads: 0 },
  ]
}

/** Each page's read takes 300 ms, as a broker call does. */
function mount(list: Page[]) {
  for (const page of list) {
    renderHook(() =>
      useBookSync(
        () => {
          page.reads += 1
          return new Promise((resolve) => setTimeout(resolve, 300))
        },
        { resyncMs: null, ...page.options }
      )
    )
  }
}

/** One F&O fill: the app's order event, then five broker updates over 400 ms. */
function fill(n: number) {
  socket.fire('order_event', { action: 'BUY', orderid: String(n) })
  for (let i = 0; i < 5; i++) {
    vi.advanceTimersByTime(80)
    socket.fire('order_update', {
      orderid: String(n),
      exchange: 'NFO',
      product: 'NRML',
      order_status: i === 4 ? 'complete' : 'open',
      filled_quantity: i === 4 ? 65 : 0,
    })
  }
}

async function run(spacingMs: number) {
  for (let n = 0; n < 20; n++) {
    act(() => fill(n))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(Math.max(0, spacingMs - 400))
    })
  }
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000)
  })
}

const calls = (list: Page[]) => list.reduce((sum, page) => sum + page.reads * page.calls, 0)
const reads = (list: Page[], name: string) => list.find((page) => page.name === name)?.reads ?? -1

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  socket.clear()
  resetOrderMemoryForTests()
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('broker calls for 20 fills, every page open and in view', () => {
  it('a basket: 20 fills one after another', async () => {
    const list = pages()
    mount(list)
    await run(400)
    // A read a second while the basket fills, and one when the broker has caught up.
    for (const name of ['positions', 'orders', 'trades', 'dashboard', 'scalping']) {
      expect(reads(list, name)).toBeLessThanOrEqual(10)
    }
    // F&O fills cannot change the holdings.
    expect(reads(list, 'holdings')).toBe(0)
    expect(calls(list)).toBeLessThanOrEqual(80)
  })

  it('20 fills spread over a minute', async () => {
    const list = pages()
    mount(list)
    await run(3_000)
    // One read per fill, the next fill's read confirming the last, and one confirm at the end.
    for (const name of ['positions', 'orders', 'trades', 'dashboard', 'scalping']) {
      expect(reads(list, name)).toBeLessThanOrEqual(21)
    }
    expect(reads(list, 'holdings')).toBe(0)
    // Well inside the 475 a minute the whole app shares (the review measured
    // about 290 to 330 for five pages before, and 470 to 510 with /scalping).
    expect(calls(list)).toBeLessThanOrEqual(170)
  })
})

describe('pages not in view', () => {
  it('read nothing while 20 fills come, and once each when seen again', async () => {
    const list = pages()
    mount(list)
    setVisibility('hidden')
    await run(3_000)
    expect(calls(list)).toBe(0)

    setVisibility('visible')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    // A minute away: every book reads once. Scalping reads on its own when
    // shown again (react-query, on focus).
    for (const name of ['positions', 'orders', 'trades', 'holdings', 'dashboard']) {
      expect(reads(list, name)).toBe(1)
    }
    expect(reads(list, 'scalping')).toBe(0)
  })
})
