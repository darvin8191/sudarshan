/**
 * In-app order alerts (M6.8): what the broker's `order_update` frames say, in
 * a trader's words, with the time it came; each order id, status and filled
 * quantity announced once, in one tab; a cancel this app asked for not
 * announced again, and a cancel-all covering only the orders working when it
 * went; a soft sound, as the app's alert settings allow; and "Sandbox" on
 * every alert in Analyze mode. The real mute is tested in
 * orderAlerts.sound.test.ts, over the real sound.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

const chime = vi.hoisted(() => ({ played: [] as string[] }))
vi.mock('./chime', () => ({
  unlockSoundOnFirstTap: () => {},
  playChime: (kind: string) => {
    chime.played.push(kind)
    return true
  },
}))

import { useAlertStore } from '@/stores/alertStore'
import type { Order } from '@/types/trading'
import {
  APP_CANCEL_MS,
  announceOrderAlert,
  CANCEL_SETTLE_MS,
  describeOrderUpdate,
  installOrderAlerts,
  noteAppCancelEvent,
  noteCancelAllFromApp,
  type OrderUpdateFrame,
  resetOrderAlertsForTests,
  setTabChannelForTests,
  TAB_DEFER_MS,
  type TabChannel,
} from './orderAlerts'
import { noteOrderBook, resetOrderMemoryForTests } from './orderFold'

const MINUS = '−'

const BOOKED: Order = {
  symbol: 'NIFTY28OCT2525900CE',
  exchange: 'NFO',
  action: 'BUY',
  quantity: 65,
  price: 120,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'NRML',
  orderid: 'X',
  order_status: 'open',
  timestamp: '20-Oct-2026 10:15:02',
}

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

/** The browser's channel between this app's tabs, stood in for. */
function fakeChannel() {
  const sent: unknown[] = []
  const channel: TabChannel & {
    sent: unknown[]
    hear: (key: string) => void
    say: (data: unknown) => void
  } = {
    sent,
    onmessage: null,
    postMessage: (message: unknown) => {
      sent.push(message)
    },
    close: () => {},
    hear: (key: string) => channel.say({ key }),
    say: (data: unknown) => channel.onmessage?.({ data } as MessageEvent),
  }
  return channel
}

let channel = fakeChannel()

/** 10:12:31 in India. */
const NOW = Date.UTC(2026, 9, 20, 4, 42, 31)

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  resetOrderAlertsForTests()
  resetOrderMemoryForTests()
  channel = fakeChannel()
  setTabChannelForTests(channel)
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
  setVisibility('visible')
  for (const fn of Object.values(toast)) fn.mockClear()
  chime.played = []
})

afterEach(() => {
  setTabChannelForTests(null)
  vi.restoreAllMocks()
  setVisibility('visible')
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
    // Only the status word where the reason goes is no reason either.
    for (const word of ['rejected', 'REJECTED', ' Rejected ']) {
      expect(
        describeOrderUpdate(
          frame({ order_status: 'rejected', filled_quantity: 0, rejection_reason: word })
        )?.description
      ).toBe('The broker gave no reason.')
    }
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

describe('announceOrderAlert', () => {
  it('shows an alert once, however often it is asked to', () => {
    const alert = describeOrderUpdate(frame({ orderid: 'ONCE' }))
    if (!alert) throw new Error('a fill is an alert')
    expect(announceOrderAlert(alert)).toBe(true)
    expect(announceOrderAlert(alert)).toBe(false)
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(chime.played).toEqual(['filled'])
  })
})

describe('installOrderAlerts', () => {
  it('toasts and sounds a fill once, however often the frame arrives or the shell remounts', async () => {
    const socket = fakeSocket()
    const remove = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({}))
    socket.fire('order_update', frame({}))
    remove()
    await Promise.resolve()
    const again = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({}))
    again()

    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('Filled · BUY 65 NIFTY28OCT2525900CE', 'orders', {
      description: '10:12:31 · 65 filled at ₹123.45 average',
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

  it('puts a rejection in front of the trader for longer, with the broker reason and the time', () => {
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
      description: '10:12:31 · Broker says: RMS: margin exceeds',
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

  it('announces a cancel made at the broker, after a moment, with the time it came', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ order_status: 'cancelled', filled_quantity: 0 }))
    expect(toast.warning).not.toHaveBeenCalled()

    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.warning.mock.calls[0][2].description).toMatch(
      /^10:12:31 · Cancelled at the broker/
    )
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
    // ...and the sandbox's own cancel, from the analyzer's update.
    noteAppCancelEvent('analyzer_update', {
      request: { api_type: 'cancelorder', orderid: '3' },
      response: { status: 'success', orderid: '3' },
    })
    socket.fire('order_update', frame({ orderid: '3', mode: 'analyze', order_status: 'cancelled' }))
    // A cancel the app did not ask for, at the same moment, still alerts.
    socket.fire('order_update', frame({ orderid: '4', order_status: 'cancelled' }))

    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.warning.mock.calls[0][0]).toBe('Cancelled · BUY 65 NIFTY28OCT2525900CE')

    // A cancel the app asked for long ago no longer quiets the broker's.
    noteAppCancelEvent('cancel_order_event', { orderid: '5', status: 'success', mode: 'live' })
    vi.advanceTimersByTime(APP_CANCEL_MS + 1)
    socket.fire('order_update', frame({ orderid: '5', order_status: 'cancelled' }))
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(2)
  })

  it('lets a cancel-all from this app quiet only the orders that were working when it went', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    noteOrderBook([
      { ...BOOKED, orderid: 'A', order_status: 'open' },
      { ...BOOKED, orderid: 'B', order_status: 'trigger pending' },
      { ...BOOKED, orderid: 'C', order_status: 'complete' },
    ])
    // Live cancel-all: one batch event, with no order ids (socketio_subscriber.on_all_orders_cancelled).
    noteAppCancelEvent('cancel_order_event', {
      status: 'success',
      orderid: '2 orders canceled',
      mode: 'live',
      batch_order: true,
    })
    socket.fire('order_update', frame({ orderid: 'A', order_status: 'cancelled' }))
    socket.fire('order_update', frame({ orderid: 'B', order_status: 'cancelled' }))
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).not.toHaveBeenCalled()

    // An order placed afterwards, and an unrelated one, cancelled by the exchange or the broker's risk checks.
    vi.advanceTimersByTime(3_500)
    socket.fire('order_update', frame({ orderid: 'D', order_status: 'open', filled_quantity: 0 }))
    socket.fire('order_update', frame({ orderid: 'D', order_status: 'cancelled' }))
    socket.fire('order_update', frame({ orderid: 'OTHER-9', order_status: 'cancelled' }))
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(2)
  })

  it('does not let a cancel-all hide a different order cancelled at the broker a moment later', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    noteAppCancelEvent('cancel_order_event', {
      status: 'success',
      orderid: '2 orders canceled',
      mode: 'live',
      batch_order: true,
    })
    vi.advanceTimersByTime(5_000)
    // An order the exchange cancels five seconds later (IOC, RMS, price band).
    socket.fire('order_update', {
      mode: 'live',
      orderid: 'OTHER-9',
      symbol: 'BANKNIFTY28OCT2556000PE',
      action: 'SELL',
      quantity: 30,
      order_status: 'cancelled',
    })
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)
  })

  it('takes the ids a sandbox cancel-all lists, and no others', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    noteAppCancelEvent('analyzer_update', {
      request: { api_type: 'cancelallorder' },
      response: { status: 'success', canceled_orders: ['S1', 'S2'] },
    })
    for (const orderid of ['S1', 'S2', 'S3']) {
      socket.fire('order_update', frame({ orderid, mode: 'analyze', order_status: 'cancelled' }))
    }
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)
  })

  it('snapshots the working orders when the order book sends its cancel-all', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    noteOrderBook([{ ...BOOKED, orderid: 'A', order_status: 'open' }])
    noteCancelAllFromApp()
    // The broker's echo arrives before the server's batch event.
    socket.fire('order_update', frame({ orderid: 'A', order_status: 'cancelled' }))
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('keeps a broker cancel still waiting when the shell remounts at once, and drops it on logout', async () => {
    const socket = fakeSocket()
    const remove = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ orderid: 'W1', order_status: 'cancelled' }))
    // A move between /trading and a normal page swaps the layout in one commit.
    remove()
    const again = installOrderAlerts(socket as never)
    await Promise.resolve()
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    expect(toast.warning).toHaveBeenCalledTimes(1)

    socket.fire('order_update', frame({ orderid: 'W2', order_status: 'cancelled' }))
    again()
    await Promise.resolve()
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    socket.fire('order_update', frame({ orderid: 'W3' }))
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('shows an alert in one tab: the tab in front tells the others', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ orderid: 'T1' }))
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(channel.sent.filter((message) => 'key' in (message as object))).toEqual([
      { key: 'T1|complete|65' },
    ])

    // Another tab showed this one first: not again here.
    channel.hear('T2|complete|65')
    socket.fire('order_update', frame({ orderid: 'T2' }))
    expect(toast.success).toHaveBeenCalledTimes(1)
  })

  it('in a tab not in front, waits a moment for the tab in front, and shows it if none does', () => {
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    setVisibility('hidden')

    socket.fire('order_update', frame({ orderid: 'H1' }))
    socket.fire('order_update', frame({ orderid: 'H2' }))
    expect(toast.success).not.toHaveBeenCalled()
    // The tab in front shows H1 and says so.
    channel.hear('H1|complete|65')
    vi.advanceTimersByTime(TAB_DEFER_MS)
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(toast.success.mock.calls[0][2].description).toMatch(/^10:12:31 · /)
  })

  it('on screen while the trader works in another window, shows an alert at once', () => {
    vi.mocked(document.hasFocus).mockReturnValue(false)
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ orderid: 'S1' }))
    expect(toast.success).toHaveBeenCalledTimes(1)
  })

  it("on screen while another of the app's tabs is in use, leaves the alert to that tab", () => {
    vi.mocked(document.hasFocus).mockReturnValue(false)
    const socket = fakeSocket()
    installOrderAlerts(socket as never)
    channel.say({ inUse: 'other-tab' })
    socket.fire('order_update', frame({ orderid: 'U1' }))
    expect(toast.success).not.toHaveBeenCalled()
    channel.hear('U1|complete|65')
    vi.advanceTimersByTime(TAB_DEFER_MS)
    expect(toast.success).not.toHaveBeenCalled()

    // That tab has left the screen: this one shows the next at once.
    channel.say({ away: 'other-tab' })
    socket.fire('order_update', frame({ orderid: 'U2' }))
    expect(toast.success).toHaveBeenCalledTimes(1)
  })

  it('tells the other tabs when it comes into use and when it leaves the screen', () => {
    installOrderAlerts(fakeSocket() as never)
    channel.sent.length = 0
    window.dispatchEvent(new Event('focus'))
    setVisibility('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    const [use, away] = channel.sent as [{ inUse?: string }, { away?: string }]
    expect(channel.sent).toHaveLength(2)
    expect(typeof use.inUse).toBe('string')
    expect(away.away).toBe(use.inUse)
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

  it('stops listening when removed, dropping a cancel still waiting', async () => {
    const socket = fakeSocket()
    const remove = installOrderAlerts(socket as never)
    socket.fire('order_update', frame({ order_status: 'cancelled' }))
    remove()
    await Promise.resolve()
    vi.advanceTimersByTime(CANCEL_SETTLE_MS)
    socket.fire('order_update', frame({ orderid: '9' }))

    expect(toast.warning).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })
})
