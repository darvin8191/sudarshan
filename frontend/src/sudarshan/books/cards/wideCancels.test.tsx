/**
 * Test day TD4 (M6.9): the wide Order Book's row Cancel sent at once, with no question, while the
 * phone's cards and Positions asked first; a mis-tap on a stop-loss's Cancel leaves a position
 * unprotected. Through the real OrderBook page on a wide screen: the row's Cancel and Cancel All
 * ask first in the cards' words, send once however they are tapped, and stay held until the book
 * shows the order final. The calls they make are the page's own, unchanged.
 */
import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Order } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getOrders: vi.fn(),
  getMultiQuotes: vi.fn(),
  cancelOrder: vi.fn(),
  cancelAllOrders: vi.fn(),
  modifyOrder: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    getQuotes: vi.fn().mockResolvedValue({ status: 'success', data: {} }),
    getDepth: vi.fn().mockResolvedValue({ status: 'success', data: { bids: [], asks: [] } }),
    cancelOrder: (...args: unknown[]) => api.cancelOrder(...args),
    cancelAllOrders: (...args: unknown[]) => api.cancelAllOrders(...args),
    modifyOrder: (...args: unknown[]) => api.modifyOrder(...args),
  },
}))
vi.mock('@/components/trading/GttTab', async () => {
  const { createElement } = await import('react')
  return { default: () => createElement('p', null, 'GTT book') }
})
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
const theme = vi.hoisted(() => ({ appMode: 'live' }))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick(theme),
}))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
    isLoading: false,
    error: null,
  }),
}))
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

import OrderBook from '@/pages/OrderBook'
import { notePushedFrame, resetOrderMemoryForTests } from '@/sudarshan/books/orderFold'
import { cancelMark, resetSentForTests, sentMarksNow } from '@/sudarshan/books/sent'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { pass, renderBook, settle } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'

vi.setConfig({ testTimeout: 20_000 })

/** A stop-loss on a NIFTY put, resting: the order a mis-tap would leave a position without. */
const STOP = {
  symbol: 'NIFTY24NOV2625800PE',
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 95,
  trigger_price: 96,
  pricetype: 'SL',
  product: 'MIS',
  orderid: '2001',
  order_status: 'open',
  timestamp: '2026-11-23 11:34:00',
} as Order
/** RELIANCE bought at a limit, resting. */
const LIMIT = {
  symbol: 'RELIANCE',
  exchange: 'NSE',
  action: 'BUY',
  quantity: 1,
  price: 1355,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'MIS',
  orderid: '2002',
  order_status: 'open',
  timestamp: '2026-11-23 11:33:00',
} as Order

const book = (...orders: Order[]) => ({ status: 'success', data: { orders, statistics: {} } })
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
const question = (title: string) => screen.getByRole('dialog', { name: title })

async function open(...orders: Order[]) {
  api.getOrders.mockResolvedValue(book(...orders))
  renderBook('/orderbook', <OrderBook />)
  await settle()
}

/** Past the half second a question takes no tap (`openGuard.ts`). */
async function settled() {
  await pass(600)
}

beforeEach(() => {
  fakeClock()
  vi.setSystemTime(new Date('2026-11-23T06:05:00Z'))
  frames.install()
  fakeScreen('wide')
  theme.appMode = 'live'
  socket.clear()
  resetSentForTests()
  resetOrderMemoryForTests()
  api.getOrders.mockReset()
  api.getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  api.cancelOrder.mockReset().mockResolvedValue({ status: 'success' })
  api.cancelAllOrders.mockReset().mockResolvedValue({ status: 'success', message: 'Done' })
  api.modifyOrder.mockReset().mockResolvedValue({ status: 'success' })
  for (const each of Object.values(toast)) each.mockReset()
})

afterEach(() => {
  OPEN_GUARD.ms = 0
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("the wide order book's row Cancel (TD4)", () => {
  it('asks first in the cards’ words, and Keep order sends nothing', async () => {
    const tap = user()
    await open(STOP, LIMIT)
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2001' }))
    expect(api.cancelOrder).not.toHaveBeenCalled()
    const asked = question('Cancel this order?')
    expect(asked).toHaveTextContent(
      'SELL 65 NIFTY 25800 PE · Trigger 96.00 · Limit 95.00. The order stops working at the exchange.'
    )
    await settled()
    await tap.click(within(asked).getByRole('button', { name: 'Keep order' }))
    await settle()
    expect(screen.queryByRole('dialog', { name: 'Cancel this order?' })).toBeNull()
    expect(api.cancelOrder).not.toHaveBeenCalled()
  })

  it('says the sandbox in Analyze mode', async () => {
    const tap = user()
    theme.appMode = 'analyzer'
    await open(LIMIT)
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2002' }))
    expect(question('Cancel this order?')).toHaveTextContent(
      'BUY 1 RELIANCE · Limit 1,355.00. The order stops working in the sandbox. Analyze mode is on, so nothing goes to Upstox.'
    )
  })

  it('sends once however it is tapped, through the page’s own cancel, and stays held', async () => {
    const tap = user()
    OPEN_GUARD.ms = 500 // the app's own half second (the tests' setup turns it off)
    await open(STOP, LIMIT)
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2001' }))
    const asked = question('Cancel this order?')
    // A tap inside the first half second lands on nothing.
    await tap.click(within(asked).getByRole('button', { name: 'Cancel order' }))
    expect(api.cancelOrder).not.toHaveBeenCalled()
    await settled()
    await tap.dblClick(within(asked).getByRole('button', { name: 'Cancel order' }))
    await settle()
    expect(api.cancelOrder).toHaveBeenCalledTimes(1)
    expect(api.cancelOrder).toHaveBeenCalledWith('2001')
    expect(toast.success).toHaveBeenCalledWith('Order cancelled: 2001', 'orders')
    // Held: the row's Cancel says why and asks nothing, while the book still shows it working.
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2001' }))
    expect(screen.queryByRole('dialog', { name: 'Cancel this order?' })).toBeNull()
    expect(toast.warning).toHaveBeenLastCalledWith(
      expect.stringMatching(
        /^A cancel for this order was sent at .+ and has not shown in the book yet/
      ),
      'orders'
    )
    expect(api.cancelOrder).toHaveBeenCalledTimes(1)
    // The other order's Cancel still asks.
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2002' }))
    expect(question('Cancel this order?')).toHaveTextContent('BUY 1 RELIANCE')
  })

  // v0.17.0 review L4: Modify stayed open and the row said nothing while the cancel was held.
  it('greys the held order’s Modify and says Cancel sent beside its status, as the cards do', async () => {
    const tap = user()
    await open(STOP, LIMIT)
    const row = () =>
      screen.getByRole('button', { name: 'Cancel order 2001' }).closest('tr') as HTMLElement
    expect(within(row()).queryByText('Cancel sent')).toBeNull()
    expect(
      screen.getByRole('button', { name: 'Modify order for NIFTY24NOV2625800PE' })
    ).toBeEnabled()
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2001' }))
    await settled()
    await tap.click(
      within(question('Cancel this order?')).getByRole('button', { name: 'Cancel order' })
    )
    await settle()
    expect(within(row()).getByText('Cancel sent')).toBeVisible()
    expect(row().querySelector('[data-sent="true"] svg')).not.toBeNull() // an icon beside the words
    expect(
      screen.getByRole('button', { name: 'Modify order for NIFTY24NOV2625800PE' })
    ).toBeDisabled()
    // The other order is untouched.
    expect(screen.getByRole('button', { name: 'Modify order for RELIANCE' })).toBeEnabled()
    const other = screen
      .getByRole('button', { name: 'Cancel order 2002' })
      .closest('tr') as HTMLElement
    expect(within(other).queryByText('Cancel sent')).toBeNull()
    expect(api.modifyOrder).not.toHaveBeenCalled()
  })

  it('lets go once the book shows the order final', async () => {
    const tap = user()
    await open(STOP)
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2001' }))
    await settled()
    await tap.click(
      within(question('Cancel this order?')).getByRole('button', { name: 'Cancel order' })
    )
    await settle()
    expect(cancelMark(sentMarksNow(), false, '2001')).toBeDefined()
    // The broker's push says it is cancelled: the hold lets go, and the row offers no Cancel.
    act(() => {
      const frame = { mode: 'live', orderid: '2001', order_status: 'cancelled' }
      notePushedFrame(frame)
      socket.fire('order_update', frame)
    })
    await settle()
    expect(cancelMark(sentMarksNow(), false, '2001')).toBeUndefined()
    expect(screen.queryByRole('button', { name: 'Cancel order 2001' })).toBeNull()
    expect(api.cancelOrder).toHaveBeenCalledTimes(1)
  })
})

describe("the wide order book's Cancel All (TD4)", () => {
  it('asks in the cards’ words, Keep orders sends nothing, and Cancel all sends once', async () => {
    const tap = user()
    await open(STOP, LIMIT)
    await tap.click(screen.getByRole('button', { name: 'Cancel All' }))
    const asked = question('Cancel all open orders?')
    expect(asked).toHaveTextContent('This will cancel all 2 open orders. This cannot be undone.')
    await settled()
    await tap.click(within(asked).getByRole('button', { name: 'Keep orders' }))
    await settle()
    expect(api.cancelAllOrders).not.toHaveBeenCalled()
    await tap.click(screen.getByRole('button', { name: 'Cancel All' }))
    await settled()
    await tap.dblClick(
      within(question('Cancel all open orders?')).getByRole('button', { name: 'Cancel all' })
    )
    await settle()
    expect(api.cancelAllOrders).toHaveBeenCalledTimes(1)
    expect(api.cancelAllOrders).toHaveBeenCalledWith()
    // Every open order's cancel is held now: a row's Cancel and Cancel All each say why.
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2002' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await tap.click(screen.getByRole('button', { name: 'Cancel All' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(toast.warning).toHaveBeenLastCalledWith(
      'A cancel is already on its way for every open order. Wait for their status to change before cancelling again.',
      'orders'
    )
    expect(api.cancelAllOrders).toHaveBeenCalledTimes(1)
  })

  it('says the sandbox in Analyze mode, and counts one order as one', async () => {
    const tap = user()
    theme.appMode = 'analyzer'
    await open(LIMIT)
    await tap.click(screen.getByRole('button', { name: 'Cancel All' }))
    expect(question('Cancel all open orders?')).toHaveTextContent(
      'This will cancel all 1 open order in the sandbox. Analyze mode is on, so nothing goes to Upstox.'
    )
  })

  it('counts only the orders whose cancel is not on its way', async () => {
    const tap = user()
    await open(STOP, LIMIT)
    await tap.click(screen.getByRole('button', { name: 'Cancel order 2001' }))
    await settled()
    await tap.click(
      within(question('Cancel this order?')).getByRole('button', { name: 'Cancel order' })
    )
    await settle()
    await tap.click(screen.getByRole('button', { name: 'Cancel All' }))
    expect(question('Cancel all open orders?')).toHaveTextContent(
      'This will cancel all 1 open order. This cannot be undone.'
    )
  })
})
