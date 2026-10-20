/**
 * In-app order alerts (M6.8): what the broker's `order_update` frames say, in
 * a trader's words; each order id, status and filled quantity announced once;
 * a cancel this app asked for not announced again; a soft sound, muted per
 * device; and "Sandbox" on every alert in Analyze mode.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

const chime = vi.hoisted(() => ({ played: [] as string[], muted: false }))
vi.mock('./chime', () => ({
  unlockSoundOnFirstTap: () => {},
  playChime: (kind: string) => {
    if (chime.muted) return false
    chime.played.push(kind)
    return true
  },
}))

import { useAlertStore } from '@/stores/alertStore'
import {
  APP_CANCEL_MS,
  CANCEL_SETTLE_MS,
  describeOrderUpdate,
  installOrderAlerts,
  noteAppCancelEvent,
  type OrderUpdateFrame,
  resetOrderAlertsForTests,
} from './orderAlerts'

const MINUS = '−'

function frame(patch: Partial<OrderUpdateFrame>): OrderUpdateFrame {
  return {
    mode: 'live',
    broker: 'upstox',
    orderid: '251020000598204',
    symbol: 'NIFTY28OCT2525900CE',
    exchange: 'NFO',
    action: 'BUY',
    quantity: 65,
    order_status: 'complete',
    filled_quantity: 65,
    average_price: 123.45,
    ...patch,
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

beforeEach(() => {
  vi.useFakeTimers()
  resetOrderAlertsForTests()
  for (const fn of Object.values(toast)) fn.mockClear()
  chime.played = []
  chime.muted = false
})

afterEach(() => {
  vi.useRealTimers()
})

describe('describeOrderUpdate', () => {
  it('says a fill in plain words, with the quantity in units and the average', () => {
    expect(describeOrderUpdate(frame({}))).toMatchObject({
      kind: 'filled',
      sandbox: false,
      title: 'Filled · BUY 65 NIFTY28OCT2525900CE',
      description: '65 filled at ₹123.45 average',
    })
  })

  it('says a partial fill: how much of how much', () => {
    expect(
      describeOrderUpdate(
        frame({ order_status: 'open', filled_quantity: 30, average_price: 123.4 })
      )
    ).toMatchObject({
      kind: 'partial',
      title: 'Partly filled · BUY NIFTY28OCT2525900CE',
      description: '30 of 65 filled at ₹123.40 average',
    })
  })

  it("quotes the broker's reason for a rejection, and says so when there is none", () => {
    const refused = describeOrderUpdate(
      frame({
        order_status: 'rejected',
        filled_quantity: 0,
        rejection_reason: 'Insufficient funds. Required margin is 12,500.',
      })
    )
    expect(refused).toMatchObject({
      kind: 'rejected',
      title: 'Rejected · BUY 65 NIFTY28OCT2525900CE',
      description: 'Broker says: Insufficient funds. Required margin is 12,500.',
    })
    expect(
      describeOrderUpdate(frame({ order_status: 'rejected', filled_quantity: 0 }))?.description
    ).toBe('The broker gave no reason.')
  })

  it('says a cancel at the broker was not made from this app, and what had filled', () => {
    expect(
      describeOrderUpdate(frame({ order_status: 'cancelled', filled_quantity: 30 }))
    ).toMatchObject({
      kind: 'cancelled',
      title: 'Cancelled · BUY 65 NIFTY28OCT2525900CE',
      description: 'Cancelled at the broker, not from this app. 30 of 65 had filled.',
    })
  })

  it('labels every alert "Sandbox" in Analyze mode, where the sandbox speaks, not the broker', () => {
    const fill = describeOrderUpdate(frame({ mode: 'analyze' }))
    expect(fill?.title).toBe('Sandbox · Filled · BUY 65 NIFTY28OCT2525900CE')
    expect(fill?.sandbox).toBe(true)
    expect(
      describeOrderUpdate(
        frame({
          mode: 'analyze',
          order_status: 'rejected',
          rejection_reason: 'Order price out of range',
        })
      )?.description
    ).toBe('Sandbox says: Order price out of range')
  })

  it('stays quiet for an order that is only placed, modified or waiting for its trigger', () => {
    for (const order_status of ['open', 'trigger pending', 'put order req received', '']) {
      expect(describeOrderUpdate(frame({ order_status, filled_quantity: 0 }))).toBeNull()
    }
    expect(describeOrderUpdate(frame({ orderid: '' }))).toBeNull()
  })

  it('never shows a code or an endpoint: only words, figures and the symbol', () => {
    const fill = describeOrderUpdate(frame({ action: 'SELL', quantity: '1,300' }))
    expect(fill?.title).toBe('Filled · SELL 1,300 NIFTY28OCT2525900CE')
    expect(`${fill?.title} ${fill?.description}`).not.toMatch(
      /order_update|status|null|undefined|\//
    )
    expect(`${fill?.title}`).not.toContain(MINUS)
  })
})

describe('installOrderAlerts', () => {
  it('toasts and sounds a fill once, however often the frame arrives or the shell remounts', () => {
    const socket = fakeSocket()
    const remove = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({}))
    socket.fire('order_update', frame({}))
    remove()
    const again = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({}))
    again()

    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('Filled · BUY 65 NIFTY28OCT2525900CE', 'orders', {
      description: '65 filled at ₹123.45 average',
      duration: 6000,
    })
    expect(chime.played).toEqual(['filled'])
  })

  it('announces each new fill of an order, and its final fill', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ order_status: 'open', filled_quantity: 30 }))
    socket.fire('order_update', frame({ order_status: 'open', filled_quantity: 45 }))
    socket.fire('order_update', frame({ order_status: 'open', filled_quantity: 45 }))
    socket.fire('order_update', frame({ order_status: 'complete', filled_quantity: 65 }))

    expect(toast.info).toHaveBeenCalledTimes(2)
    expect(toast.success).toHaveBeenCalledTimes(1)
  })

  it('puts a rejection in front of the trader for longer, with the broker reason', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire(
      'order_update',
      frame({
        order_status: 'rejected',
        filled_quantity: 0,
        rejection_reason: 'RMS: margin exceeds',
      })
    )

    expect(toast.error).toHaveBeenCalledWith('Rejected · BUY 65 NIFTY28OCT2525900CE', 'orders', {
      description: 'Broker says: RMS: margin exceeds',
      duration: 12000,
    })
    expect(chime.played).toEqual(['rejected'])
  })

  it('files a sandbox alert under the analyzer alerts', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ mode: 'analyze' }))

    expect(toast.success).toHaveBeenCalledWith(
      'Sandbox · Filled · BUY 65 NIFTY28OCT2525900CE',
      'analyzer',
      expect.anything()
    )
  })

  it('announces a cancel made at the broker, after a moment', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ order_status: 'cancelled', filled_quantity: 0 }))
    expect(toast.warning).not.toHaveBeenCalled()

    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)
  })

  it('does not announce a cancel this app asked for, whichever arrives first', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)

    // The server's own cancel event first...
    noteAppCancelEvent('cancel_order_event', { orderid: '1', status: 'success', mode: 'live' })
    socket.fire('order_update', frame({ orderid: '1', order_status: 'cancelled' }))
    // ...or the broker's echo a moment before it.
    socket.fire('order_update', frame({ orderid: '2', order_status: 'cancelled' }))
    noteAppCancelEvent('cancel_order_event', { orderid: '2', status: 'success', mode: 'live' })
    // A sandbox cancel all, from the analyzer's update.
    noteAppCancelEvent('analyzer_update', {
      request: { api_type: 'cancelallorder' },
      response: { status: 'success', canceled_orders: ['3'] },
    })
    socket.fire('order_update', frame({ orderid: '3', mode: 'analyze', order_status: 'cancelled' }))

    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).not.toHaveBeenCalled()

    // Long after, a cancel nobody here asked for is the broker's again.
    vi.advanceTimersByTime(APP_CANCEL_MS)
    socket.fire('order_update', frame({ orderid: '4', order_status: 'cancelled' }))
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)
  })

  it('still shows the alert when the sound is muted', () => {
    chime.muted = true
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({}))

    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(chime.played).toEqual([])
  })

  it('plays no sound when the app alert settings turn order alerts or alert sounds off', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    try {
      useAlertStore.getState().setSoundEnabled(false)
      socket.fire('order_update', frame({ orderid: '11' }))
      useAlertStore.getState().setSoundEnabled(true)
      useAlertStore.getState().setCategoryEnabled('orders', false)
      socket.fire('order_update', frame({ orderid: '12' }))
      expect(chime.played).toEqual([])

      // Sandbox alerts follow the analyzer category, which is still on.
      socket.fire('order_update', frame({ orderid: '13', mode: 'analyze' }))
      expect(chime.played).toEqual(['filled'])
    } finally {
      useAlertStore.getState().resetToDefaults()
    }
  })

  it('stops listening when removed, dropping a cancel still waiting', () => {
    const socket = fakeSocket()
    const remove = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ order_status: 'cancelled' }))
    remove()
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    socket.fire('order_update', frame({ orderid: '9' }))

    expect(toast.warning).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })
})
