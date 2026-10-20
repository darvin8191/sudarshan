/**
 * The Order book, live (M6.1): the broker's own updates change a row the
 * moment they are pushed, a re-read follows them, a re-read that trails the
 * push cannot put a filled order back to "open" (nor can a frame delivered
 * late), a page opened just after a fill shows the fill, an answer a newer read
 * has overtaken is dropped, and the LTP of working orders comes from the feed,
 * never from a timer.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Order } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getOrders = vi.fn()
const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getOrders: (...args: unknown[]) => getOrders(...args),
    getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args),
    getQuotes: vi.fn().mockResolvedValue({ status: 'success', data: {} }),
    getDepth: vi.fn().mockResolvedValue({ status: 'success', data: { bids: [], asks: [] } }),
    cancelOrder: vi.fn(),
    cancelAllOrders: vi.fn(),
    modifyOrder: vi.fn(),
  },
}))
vi.mock('@/components/trading/GttTab', () => ({ default: () => null }))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick({ appMode: 'live' }),
}))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
const calendar = {
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}
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

import { notePushedFrame, resetOrderMemoryForTests } from '@/sudarshan/books/orderFold'
import { feed } from '@/test/marketDataHarness'
import { fakeScreen } from '@/test/screenWidth'
import { render, screen, within } from '@/test/test-utils'
import OrderBook from './OrderBook'

const CALL = 'NIFTY28OCT2525900CE'

const OPEN: Order = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'BUY',
  quantity: 65,
  price: 120,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'NRML',
  orderid: '251020000598204',
  order_status: 'open',
  timestamp: '20-Oct-2026 10:15:02',
}

const STATS = {
  total_buy_orders: 1,
  total_sell_orders: 0,
  total_completed_orders: 0,
  total_open_orders: 1,
  total_rejected_orders: 0,
}

function book(...orders: Order[]) {
  return { status: 'success', data: { orders, statistics: STATS } }
}

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function statusOf(orderid: string) {
  const row = screen.getByText(orderid).closest('tr') as HTMLElement
  return within(row)
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
  socket.clear()
  resetOrderMemoryForTests()
  getOrders.mockReset().mockResolvedValue(book(OPEN))
  getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Order book, live', () => {
  it("re-reads the book when the broker pushes an order's update", async () => {
    render(<OrderBook />)
    await pass(0)
    expect(getOrders).toHaveBeenCalledTimes(1)

    act(() =>
      socket.fire('order_update', { mode: 'live', orderid: OPEN.orderid, order_status: 'open' })
    )
    await pass(500)
    expect(getOrders).toHaveBeenCalledTimes(2)
  })

  it('changes the row on the push, and a re-read that still says open cannot put it back', async () => {
    render(<OrderBook />)
    await pass(0)
    expect(statusOf(OPEN.orderid).getByText('open')).toBeInTheDocument()

    const complete = {
      mode: 'live',
      orderid: OPEN.orderid,
      order_status: 'complete',
      filled_quantity: 65,
    }
    // The shell's alert listener remembers it app-wide; the page folds it into its row.
    act(() => {
      notePushedFrame(complete)
      socket.fire('order_update', complete)
    })
    expect(statusOf(OPEN.orderid).getByText('complete')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Cancel order ${OPEN.orderid}` })).toBeNull()

    // Upstox's REST book trails its push by a second or two.
    await pass(1_000)
    expect(getOrders).toHaveBeenCalledTimes(2)
    expect(statusOf(OPEN.orderid).getByText('complete')).toBeInTheDocument()

    // A frame delivered late does not undo the fill either.
    act(() =>
      socket.fire('order_update', { mode: 'live', orderid: OPEN.orderid, order_status: 'open' })
    )
    expect(statusOf(OPEN.orderid).getByText('complete')).toBeInTheDocument()
  })

  it('shows the fill on a page opened just after it, and reads again once the broker has caught up', async () => {
    notePushedFrame({ mode: 'live', orderid: OPEN.orderid, order_status: 'complete' })
    await pass(1_000)
    render(<OrderBook />)
    await pass(0)
    // Its first read still says open; the push says complete.
    expect(statusOf(OPEN.orderid).getByText('complete')).toBeInTheDocument()

    getOrders.mockResolvedValue(book({ ...OPEN, order_status: 'complete' }))
    await pass(2_000)
    expect(getOrders).toHaveBeenCalledTimes(2)
    expect(statusOf(OPEN.orderid).getByText('complete')).toBeInTheDocument()
  })

  it('keeps Cancel and Modify for a status the book does not use, such as "not cancelled"', async () => {
    render(<OrderBook />)
    await pass(0)
    act(() =>
      socket.fire('order_update', {
        mode: 'live',
        orderid: OPEN.orderid,
        order_status: 'not cancelled',
      })
    )
    expect(screen.getByRole('button', { name: `Cancel order ${OPEN.orderid}` })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Modify order for ${CALL}` })).toBeInTheDocument()
  })

  it('drops an answer a newer read has overtaken', async () => {
    let answerFirst: (value: unknown) => void = () => {}
    getOrders.mockReset()
    getOrders.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answerFirst = resolve
        })
    )
    getOrders.mockResolvedValue(book({ ...OPEN, order_status: 'complete' }))
    render(<OrderBook />)
    await pass(0)

    // A newer read, after a fill, answers first.
    act(() => socket.fire('order_event', {}))
    await pass(500)
    expect(getOrders).toHaveBeenCalledTimes(2)
    // The first, older answer arrives late: it must not replace the newer book.
    await act(async () => answerFirst(book(OPEN)))
    expect(statusOf(OPEN.orderid).getByText('complete')).toBeInTheDocument()
  })

  it('moves the LTP of a working order with the feed and asks REST for prices only once', async () => {
    render(<OrderBook />)
    await pass(0)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    act(() => feed.push(CALL, 'NFO', { ltp: 121.5 }))
    expect(screen.getAllByText('₹121.50').length).toBeGreaterThan(0)
    await pass(5 * 60_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)
  })
})

// SUDARSHAN M4.15, M6.9: on a phone an order's card opens a sheet whose Chart
// opens the phone chart; a wide screen keeps upstream's link into the terminal.
describe('Order book chart link', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("opens the phone chart from an order's sheet on a phone, the terminal on a wide screen", async () => {
    const screenNow = fakeScreen('phone')
    render(<OrderBook />)
    await pass(0)
    act(() => screen.getByRole('button', { name: /NIFTY 25900 CE/ }).click())
    expect(screen.getByRole('link', { name: `Open the chart for ${CALL} on NFO` })).toHaveAttribute(
      'href',
      `/chart/NFO/${CALL}`
    )

    act(() => screenNow.turn('wide'))
    expect(screen.getByRole('link', { name: `Open chart for ${CALL} on NFO` })).toHaveAttribute(
      'href',
      `/trading?symbol=${CALL}&exchange=NFO`
    )
  })
})
