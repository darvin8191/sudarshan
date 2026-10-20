/**
 * The order alert sound over the real sound module (M6.8): a fill sounds once
 * the first tap has started the audio, the per-device mute in Profile >
 * Display silences it while the alert still shows, and one sound per moment is
 * shared with the app's own placement sound, so an order placed and filled at
 * once (as a sandbox market order is) sounds once.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

import { claimOrderSound, resetChimeForTests, setOrderSoundMuted } from './chime'
import {
  installOrderAlerts,
  type OrderUpdateFrame,
  resetOrderAlertsForTests,
  setTabChannelForTests,
} from './orderAlerts'
import { resetOrderMemoryForTests } from './orderFold'

class FakeParam {
  setValueAtTime() {}
  exponentialRampToValueAtTime() {}
}

class FakeNode {
  gain = new FakeParam()
  frequency = new FakeParam()
  type = 'sine'
  onended: (() => void) | null = null
  connect() {
    return this
  }
  disconnect() {}
  start() {}
  stop() {}
}

let notes = 0

class FakeContext {
  state: 'suspended' | 'running' = 'running'
  currentTime = 0
  destination = {}
  onstatechange: (() => void) | null = null
  resume() {
    this.state = 'running'
    return Promise.resolve()
  }
  createGain() {
    return new FakeNode()
  }
  createOscillator() {
    notes += 1
    return new FakeNode()
  }
}

function fakeSocket() {
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
  }
}

function fill(orderid: string, mode = 'live'): OrderUpdateFrame {
  return {
    mode,
    orderid,
    symbol: 'NIFTY28OCT2525900CE',
    exchange: 'NFO',
    action: 'BUY',
    quantity: 65,
    order_status: 'complete',
    filled_quantity: 65,
    average_price: 120,
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  notes = 0
  vi.stubGlobal('AudioContext', FakeContext)
  resetChimeForTests()
  resetOrderAlertsForTests()
  resetOrderMemoryForTests()
  setTabChannelForTests({ postMessage: () => {}, onmessage: null, close: () => {} })
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
  setOrderSoundMuted(false)
  for (const fn of Object.values(toast)) fn.mockClear()
})

afterEach(() => {
  setOrderSoundMuted(false)
  setTabChannelForTests(null)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('order alert sound', () => {
  it('sounds a fill once the first tap has started the audio', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', fill('1'))
    // Chrome allows no sound before the person has touched the page.
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(notes).toBe(0)

    document.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(1_000)
    socket.fire('order_update', fill('2'))
    expect(notes).toBe(1)
  })

  it('is silenced by the per-device mute, while the alert itself still shows', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    document.dispatchEvent(new Event('pointerdown'))
    setOrderSoundMuted(true)

    socket.fire('order_update', fill('3'))
    socket.fire('order_update', fill('4', 'analyze'))
    expect(toast.success).toHaveBeenCalledTimes(2)
    expect(notes).toBe(0)

    setOrderSoundMuted(false)
    vi.advanceTimersByTime(1_000)
    socket.fire('order_update', fill('5'))
    expect(notes).toBe(1)
  })

  it('shares one sound per moment with the placement sound', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    document.dispatchEvent(new Event('pointerdown'))

    // The app's placement sound has just played for this order...
    expect(claimOrderSound()).toBe(true)
    // ...so its fill a moment later shows, quietly.
    vi.advanceTimersByTime(200)
    socket.fire('order_update', fill('6', 'analyze'))
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(notes).toBe(0)

    // A fill on its own sounds, and the placement sound then waits its turn.
    vi.advanceTimersByTime(2_000)
    socket.fire('order_update', fill('7'))
    expect(notes).toBe(1)
    expect(claimOrderSound()).toBe(false)
  })
})
