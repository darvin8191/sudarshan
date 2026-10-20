/**
 * The order book on a phone (M6.9), through the real OrderBook page at 390px:
 * a card per order with its side and status in words and icons, filled of
 * ordered, the price it works at, the last price of a working order tick by
 * tick, filter chips, and a sheet whose Modify, Cancel and Cancel all are the
 * page's own. A rejection says the broker's reason. Back closes what is open.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
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
  return { default: () => createElement('p', null, 'GTT book, as upstream shows it') }
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
vi.mock('@/utils/toast', () => ({
  showToast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))
const calendar = vi.hoisted(() => ({
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))

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
import { back, pass, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { fakeScreen } from '@/test/screenWidth'

vi.setConfig({ testTimeout: 15_000 })

const CALL = 'NIFTY27OCT2625900CE'
const PUT = 'NIFTY27OCT2625800PE'

const WORKING = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 168,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'MIS',
  orderid: '1001',
  order_status: 'open',
  timestamp: '2026-10-20 10:41:51',
  filled_quantity: 0,
  pending_quantity: 65,
} as Order
const STOP = {
  symbol: PUT,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 95,
  trigger_price: 96,
  pricetype: 'SL',
  product: 'MIS',
  orderid: '1002',
  order_status: 'trigger pending',
  timestamp: '2026-10-20 10:30:00',
} as Order
const FILLED = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'BUY',
  quantity: 65,
  price: 0,
  trigger_price: 0,
  pricetype: 'MARKET',
  product: 'MIS',
  orderid: '1003',
  order_status: 'complete',
  average_price: 142.5,
  filled_quantity: 65,
  timestamp: '2026-10-20 10:12:18',
} as Order
const REFUSED = {
  symbol: 'BANKNIFTY27OCT2658000CE',
  exchange: 'NFO',
  action: 'BUY',
  quantity: 30,
  price: 310,
  trigger_price: 305,
  pricetype: 'SL',
  product: 'MIS',
  orderid: '1004',
  order_status: 'rejected',
  rejection_reason: 'RMS:Margin Exceeds,Required:14250.00, Available:9120.55',
  timestamp: '2026-10-20 09:22:13',
} as Order
const CANCELLED = {
  symbol: 'SBIN',
  exchange: 'NSE',
  action: 'BUY',
  quantity: 100,
  price: 800,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'CNC',
  orderid: '1005',
  order_status: 'cancelled',
  filled_quantity: 40,
  timestamp: '2026-10-20 09:15:30',
} as Order

const ALL = [CANCELLED, FILLED, WORKING, REFUSED, STOP]

/** A working order as Upstox's REST book has it (M6.9): no fill fields. */
const UPSTOX_WORKING = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 168,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'MIS',
  orderid: '1001',
  order_status: 'open',
  timestamp: '2026-10-20 10:41:51',
} as Order

/** A frame as the shell and the page both receive it. */
function push(frame: Record<string, unknown>) {
  const full = { mode: 'live', ...frame }
  // As the shell's order-alert listener does for every frame (orderAlerts.ts).
  notePushedFrame(full)
  act(() => socket.fire('order_update', full))
}

/** Let the re-read behind an order event land (useBookSync), with the book still behind. */
async function reread() {
  const before = api.getOrders.mock.calls.length
  await pass(3_000)
  await settle()
  expect(api.getOrders.mock.calls.length).toBeGreaterThan(before)
}

function book(...orders: Order[]) {
  return {
    status: 'success',
    data: {
      orders,
      statistics: {
        total_buy_orders: 0,
        total_sell_orders: 0,
        total_completed_orders: 0,
        total_open_orders: 0,
        total_rejected_orders: 0,
      },
    },
  }
}

async function renderPhone(...orders: Order[]) {
  api.getOrders.mockResolvedValue(book(...orders))
  fakeScreen('phone')
  const view = renderBook('/orderbook', <OrderBook />)
  await settle()
  return view
}

function orderCard(orderid: string) {
  const item = document.querySelector(`li[data-order="${orderid}"]`)
  if (!item) throw new Error(`no card for order ${orderid}`)
  return within(item as HTMLElement).getByRole('button')
}

function shownOrders(): string[] {
  return [...document.querySelectorAll('li[data-order]')].map(
    (item) => item.getAttribute('data-order') ?? ''
  )
}

function user() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
}

beforeEach(() => {
  fakeClock()
  frames.install()
  feed.reset()
  socket.clear()
  resetOrderMemoryForTests()
  theme.appMode = 'live'
  api.getOrders.mockReset()
  api.getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  api.cancelOrder.mockReset().mockResolvedValue({ status: 'success' })
  api.cancelAllOrders.mockReset().mockResolvedValue({ status: 'success', message: 'Done' })
  api.modifyOrder.mockReset().mockResolvedValue({ status: 'success' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Order book on a phone: the cards', () => {
  it('shows each order newest first, its side and status in words and icons', async () => {
    await renderPhone(...ALL)
    expect(screen.queryByRole('table')).toBeNull()
    expect(shownOrders()).toEqual(['1001', '1002', '1003', '1004', '1005'])

    const working = orderCard('1001')
    expect(working).toHaveTextContent('SELL')
    expect(working).toHaveTextContent('NIFTY 25900 CE')
    expect(working).toHaveTextContent('NFO · 27 Oct 2026 · MIS')
    expect(working).toHaveTextContent('10:41:51')
    // Nothing filled yet: the quantity alone, never "0/65".
    expect(working).toHaveTextContent('65 · Limit 168.00')
    expect(working).not.toHaveTextContent('0/65')

    const chip = (orderid: string) =>
      orderCard(orderid).querySelector('[data-status]') as HTMLElement
    expect(chip('1001')).toHaveTextContent('OPEN')
    expect(chip('1001')).toHaveClass('text-[color:var(--warning-text)]')
    expect(chip('1002')).toHaveTextContent('TRIGGER PENDING')
    expect(chip('1002')).toHaveClass('text-[color:var(--warning-text)]')
    expect(chip('1003')).toHaveTextContent('COMPLETE')
    expect(chip('1003')).toHaveClass('text-[color:var(--up)]')
    expect(chip('1004')).toHaveTextContent('REJECTED')
    expect(chip('1004')).toHaveClass('text-[color:var(--down)]')
    expect(chip('1005')).toHaveTextContent('CANCELLED')
    expect(chip('1005')).toHaveClass('text-[color:var(--neutral)]')
    // Every chip has its icon as well as its word.
    for (const orderid of ['1001', '1002', '1003', '1004', '1005']) {
      expect(chip(orderid).querySelector('svg')).not.toBeNull()
    }

    expect(orderCard('1002')).toHaveTextContent('Trigger 96.00 · Limit 95.00')
    expect(orderCard('1003')).toHaveTextContent('65/65 · Market · Avg 142.50')
    expect(orderCard('1005')).toHaveTextContent('40/100 · Limit 800.00')
  })

  it('shows the last price of a working order only, tick by tick', async () => {
    await renderPhone(WORKING, FILLED)
    act(() => {
      feed.push(CALL, 'NFO', { ltp: 151.2 })
      frames.flush()
    })
    act(() => {
      feed.push(CALL, 'NFO', { ltp: 152.4 })
      frames.flush()
    })
    expect(orderCard('1001').querySelector('[data-tick="up"]')).toHaveTextContent('152.40')
    expect(orderCard('1003')).not.toHaveTextContent('LTP')
    expect(screen.getByText('Real-time')).toBeInTheDocument()
  })

  it('gives the feed line a row of its own, apart from Refresh and Cancel all', async () => {
    await renderPhone(WORKING, STOP)
    act(() => {
      feed.push(CALL, 'NFO', { ltp: 151.2 })
      frames.flush()
    })
    const actions = screen.getByTestId('order-actions')
    const cancelAll = within(actions).getByRole('button', { name: 'Cancel all (2)' })
    expect(cancelAll).toHaveClass('whitespace-nowrap')
    expect(within(actions).getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
    expect(actions).not.toHaveTextContent('Real-time')
    const line = screen.getByText('Real-time').closest('p') as HTMLElement
    expect(line).toHaveTextContent('LTP')
    expect(actions.contains(line)).toBe(false)
  })

  it('filters by Open, Executed, Rejected and Cancelled, with counts', async () => {
    const tap = user()
    await renderPhone(...ALL)
    const chips = screen.getByRole('group', { name: 'Show orders' })
    const chip = (name: RegExp) => within(chips).getByRole('button', { name })
    expect(chip(/^All/)).toHaveTextContent('All5')
    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true')

    await tap.click(chip(/^Open/))
    expect(chip(/^Open/)).toHaveAttribute('aria-pressed', 'true')
    expect(shownOrders()).toEqual(['1001', '1002'])
    await tap.click(chip(/^Executed/))
    expect(shownOrders()).toEqual(['1003'])
    await tap.click(chip(/^Rejected/))
    expect(shownOrders()).toEqual(['1004'])
    await tap.click(chip(/^Cancelled/))
    expect(shownOrders()).toEqual(['1005'])
    await tap.click(chip(/^All/))
    expect(shownOrders()).toHaveLength(5)
  })

  it('says plainly when a filter has nothing', async () => {
    const tap = user()
    await renderPhone(WORKING)
    await tap.click(screen.getByRole('button', { name: /^Cancelled/ }))
    expect(screen.getByText('No cancelled orders')).toBeInTheDocument()
  })

  it('says plainly when there are no orders', async () => {
    await renderPhone()
    expect(screen.getByText('No orders today')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Cancel all/ })).toBeDisabled()
  })

  it('changes a card the moment the broker pushes a fill', async () => {
    await renderPhone(WORKING)
    act(() =>
      socket.fire('order_update', {
        mode: 'live',
        orderid: '1001',
        order_status: 'complete',
        filled_quantity: 65,
      })
    )
    expect(orderCard('1001').querySelector('[data-status]')).toHaveTextContent('COMPLETE')
  })

  it('shows a pushed fill’s count and average at once, over a re-read that is behind', async () => {
    await renderPhone(UPSTOX_WORKING)
    push({ orderid: '1001', order_status: 'complete', filled_quantity: 65, average_price: 168.05 })
    const chip = () => orderCard('1001').querySelector('[data-status]')
    expect(chip()).toHaveTextContent('COMPLETE')
    expect(orderCard('1001')).toHaveTextContent('65/65 · Limit 168.00 · Avg 168.05')

    await reread()
    expect(chip()).toHaveTextContent('COMPLETE')
    expect(orderCard('1001')).toHaveTextContent('65/65 · Limit 168.00 · Avg 168.05')
  })

  it('moves the count on a pushed part fill, and keeps it after the re-read', async () => {
    await renderPhone(UPSTOX_WORKING)
    push({ orderid: '1001', order_status: 'open', filled_quantity: 30, average_price: 168 })
    expect(orderCard('1001')).toHaveTextContent('30/65 · Limit 168.00 · Avg 168.00')

    await reread()
    expect(orderCard('1001')).toHaveTextContent('30/65 · Limit 168.00 · Avg 168.00')
    // A frame from before the fill, delivered late, does not take it back.
    push({ orderid: '1001', order_status: 'open', filled_quantity: 0 })
    expect(orderCard('1001')).toHaveTextContent('30/65')
  })

  it('keeps TRIGGER PENDING from the broker over the REST book’s "open"', async () => {
    // Upstox's REST book says "open" for a stop order waiting for its trigger.
    await renderPhone({ ...STOP, order_status: 'open' } as Order)
    const chip = () => orderCard('1002').querySelector('[data-status]')
    expect(chip()).toHaveTextContent('OPEN')
    push({ orderid: '1002', order_status: 'trigger pending' })
    expect(chip()).toHaveTextContent('TRIGGER PENDING')

    await reread()
    await pass(10_000)
    expect(chip()).toHaveTextContent('TRIGGER PENDING')
  })

  it('keeps the GTT tab as upstream shows it', async () => {
    const tap = user()
    await renderPhone(WORKING)
    await tap.click(screen.getByRole('tab', { name: 'GTT' }))
    expect(await screen.findByText('GTT book, as upstream shows it')).toBeInTheDocument()
  })
})

describe('Order book on a phone: the sheet and its actions', () => {
  it('says why the broker refused an order, in its own words', async () => {
    const tap = user()
    await renderPhone(REFUSED)
    await tap.click(orderCard('1004'))
    const sheet = screen.getByRole('dialog', { name: 'BANKNIFTY 58000 CE' })
    expect(within(sheet).getByTestId('rejection-reason')).toHaveTextContent(
      'Broker says: RMS:Margin Exceeds,Required:14250.00, Available:9120.55'
    )
    expect(within(sheet).queryByRole('button', { name: 'Modify' })).toBeNull()
    expect(within(sheet).queryByRole('button', { name: 'Cancel order' })).toBeNull()
    expect(within(sheet).getByRole('link', { name: /Open the chart for/ })).toHaveAttribute(
      'href',
      '/chart/NFO/BANKNIFTY27OCT2658000CE'
    )
  })

  it('says the broker’s reason for a pushed rejection, before and after the re-read', async () => {
    const tap = user()
    await renderPhone(UPSTOX_WORKING)
    push({
      orderid: '1001',
      order_status: 'rejected',
      filled_quantity: 0,
      rejection_reason: 'RMS:Margin Exceeds,Required:14250.00, Available:9120.55',
    })
    await tap.click(orderCard('1001'))
    const reason = () => screen.getByTestId('rejection-reason')
    expect(reason()).toHaveTextContent('Broker says: RMS:Margin Exceeds')

    await reread()
    expect(reason()).toHaveTextContent('Broker says: RMS:Margin Exceeds')
  })

  it('says the sandbox refused it in Analyze mode, and when no reason was given', async () => {
    const tap = user()
    theme.appMode = 'analyzer'
    await renderPhone({ ...REFUSED, rejection_reason: '' } as Order)
    await tap.click(orderCard('1004'))
    expect(screen.getByTestId('rejection-reason')).toHaveTextContent('The sandbox gave no reason.')
  })

  it('cancels through the page’s own cancel call, after a confirmation', async () => {
    const tap = user()
    await renderPhone(WORKING, FILLED)
    await tap.click(orderCard('1001'))
    await tap.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel order' })
    )
    const confirm = screen.getByRole('dialog', { name: 'Cancel this order?' })
    expect(confirm).toHaveTextContent('SELL 65 NIFTY 25900 CE · Limit 168.00')
    await tap.dblClick(within(confirm).getByRole('button', { name: 'Cancel order' }))
    expect(api.cancelOrder).toHaveBeenCalledTimes(1)
    expect(api.cancelOrder).toHaveBeenCalledWith('1001')
    await waitFor(() => expect(where()).toBe('/orderbook'))
  })

  it('names the rest of a part-filled order in the cancel confirmation', async () => {
    const tap = user()
    await renderPhone({ ...UPSTOX_WORKING, quantity: 100 } as Order)
    push({ orderid: '1001', order_status: 'open', filled_quantity: 40, pending_quantity: 60 })
    await tap.click(orderCard('1001'))
    await tap.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel order' })
    )
    expect(screen.getByRole('dialog', { name: 'Cancel this order?' })).toHaveTextContent(
      'SELL the remaining 60 of 100 NIFTY 25900 CE · Limit 168.00'
    )
  })

  it('makes the same cancel call as the wide table’s button', async () => {
    const tap = user()
    api.getOrders.mockResolvedValue(book(WORKING))
    fakeScreen('wide')
    renderBook('/orderbook', <OrderBook />)
    await settle()
    await tap.click(screen.getByRole('button', { name: 'Cancel order 1001' }))
    expect(api.cancelOrder).toHaveBeenLastCalledWith('1001')
  })

  it('opens the page’s own modify dialog, and Back closes it', async () => {
    const tap = user()
    await renderPhone(WORKING)
    await tap.click(orderCard('1001'))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Modify' }))
    const dialog = screen.getByRole('dialog', { name: /Modify Order/ })
    expect(dialog).toHaveTextContent('1001')
    expect(within(dialog).getByLabelText('Price')).toHaveValue(168)
    expect(where()).toBe('/orderbook?act=modify&row=1001')

    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(where()).toBe('/orderbook')
    expect(api.modifyOrder).not.toHaveBeenCalled()
  })

  it('keeps the modify dialog, with what was typed, while the phone turns and back', async () => {
    const tap = user()
    api.getOrders.mockResolvedValue(book(WORKING))
    const screenNow = fakeScreen('phone')
    renderBook('/orderbook', <OrderBook />)
    await settle()
    await tap.click(orderCard('1001'))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Modify' }))
    const priceBox = () =>
      within(screen.getByRole('dialog', { name: /Modify Order/ })).getByLabelText('Price')
    await tap.clear(priceBox())
    await tap.type(priceBox(), '170')

    // The dialog hides the page under it from assistive tech, so the page is found by tag.
    act(() => screenNow.turn('wide'))
    await settle()
    expect(document.querySelector('table')).not.toBeNull()
    expect(priceBox()).toHaveValue(170)

    act(() => screenNow.turn('phone'))
    await settle()
    expect(document.querySelector('li[data-order="1001"]')).not.toBeNull()
    expect(priceBox()).toHaveValue(170)
  })

  it('takes the entry away when the modify dialog closes itself', async () => {
    const tap = user()
    await renderPhone(WORKING)
    await tap.click(orderCard('1001'))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Modify' }))
    const dialog = screen.getByRole('dialog', { name: /Modify Order/ })
    await tap.click(within(dialog).getByRole('button', { name: 'Modify Order' }))
    expect(api.modifyOrder).toHaveBeenCalledTimes(1)
    expect(api.modifyOrder.mock.calls[0][0]).toBe('1001')
    await waitFor(() => expect(where()).toBe('/orderbook'))
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('cancels all through the page’s cancel-all, after its confirmation', async () => {
    const tap = user()
    await renderPhone(WORKING, STOP, FILLED)
    const all = screen.getByRole('button', { name: 'Cancel all (2)' })
    await tap.click(all)
    const confirm = screen.getByRole('dialog', { name: 'Cancel all open orders?' })
    expect(confirm).toHaveTextContent('This will cancel all 2 open orders')
    await tap.click(within(confirm).getByRole('button', { name: 'Cancel all' }))
    expect(api.cancelAllOrders).toHaveBeenCalledTimes(1)
  })

  it('closes the sheet on Back, then leaves the page', async () => {
    const tap = user()
    await renderPhone(WORKING)
    await tap.click(orderCard('1001'))
    expect(where()).toBe('/orderbook?row=1001')
    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(where()).toBe('/orderbook')
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('cancels a cancel on Back, with nothing sent', async () => {
    const tap = user()
    await renderPhone(WORKING)
    await tap.click(orderCard('1001'))
    await tap.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel order' })
    )
    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(api.cancelOrder).not.toHaveBeenCalled()
  })
})

describe('Order book on a wide screen', () => {
  it('keeps upstream’s table and toolbar', async () => {
    api.getOrders.mockResolvedValue(book(WORKING, FILLED))
    fakeScreen('wide')
    renderBook('/orderbook', <OrderBook />)
    await settle()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Modify order for NIFTY27OCT2625900CE' })
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /Cancel All/ })).toBeInTheDocument()
    expect(document.querySelector('li[data-order]')).toBeNull()
  })
})
