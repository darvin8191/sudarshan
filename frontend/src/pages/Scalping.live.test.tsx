/**
 * The scalping terminal's books, live (M6.3): a fill costs one reload of the
 * books for all its events (the app's order event and the broker's updates
 * together), and one more once the broker's books have caught up; nothing is
 * reloaded while the page is hidden; and the MultiQuotes snapshot is read
 * once per symbol set, never every 30 seconds.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
vi.mock('@/components/scalping/ScalpChart', () => ({ ScalpChart: () => null }))
vi.mock('@/components/scalping/SetSLDialog', () => ({ SetSLDialog: () => null }))

const getPositions = vi.fn()
const getOrders = vi.fn()
const getTrades = vi.fn()
const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => getPositions(...args),
    getOrders: (...args: unknown[]) => getOrders(...args),
    getTrades: (...args: unknown[]) => getTrades(...args),
    getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args),
  },
}))
vi.mock('@/api/scalping', () => {
  const empty = () => Promise.resolve({ status: 'success', data: [] })
  return {
    scalpingApi: {
      search: empty,
      getAllUnderlyings: empty,
      getExpiry: empty,
      getStrikes: () => Promise.resolve({ status: 'success', chain: [] }),
      futures: empty,
      getTracked: empty,
      getSLStates: empty,
      saveSL: empty,
      deleteSL: empty,
      placeOrder: empty,
      closeAll: empty,
      cancelAll: empty,
      closeLeg: empty,
    },
  }
})
vi.mock('@/stores/authStore', () => ({
  useAuthStore: (pick?: (state: { apiKey: string }) => unknown) =>
    pick ? pick({ apiKey: 'test-api-key' }) : { apiKey: 'test-api-key' },
}))
vi.mock('@/stores/themeStore', () => ({
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick({ appMode: 'live' }),
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

import { feed } from '@/test/marketDataHarness'
import { render } from '@/test/test-utils'
import Scalping from './Scalping'

const CALL = 'NIFTY28OCT2525900CE'

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

function renderScalping() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return render(<Scalping />, { wrapper } as never)
}

/** One fill: the app's order event, then five broker updates over 400 ms. */
function fill() {
  act(() => socket.fire('order_event', { action: 'BUY', symbol: CALL, orderid: '1' }))
  for (let i = 0; i < 5; i++) {
    act(() => {
      vi.advanceTimersByTime(80)
      socket.fire('order_update', {
        mode: 'live',
        orderid: '1',
        order_status: i === 4 ? 'complete' : 'open',
      })
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
  socket.clear()
  getPositions.mockReset().mockResolvedValue({
    status: 'success',
    data: [
      {
        symbol: CALL,
        exchange: 'NFO',
        product: 'NRML',
        quantity: 65,
        average_price: 120,
        ltp: 130,
        pnl: 650,
      },
    ],
  })
  getOrders.mockReset().mockResolvedValue({ status: 'success', data: { orders: [] } })
  getTrades.mockReset().mockResolvedValue({ status: 'success', data: [] })
  getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('Scalping books, live', () => {
  it('reloads the books once for a fill, and once more when the broker has caught up', async () => {
    renderScalping()
    await pass(0)
    expect(getPositions).toHaveBeenCalledTimes(1)

    fill()
    await pass(1_000)
    expect(getPositions).toHaveBeenCalledTimes(2)
    expect(getOrders).toHaveBeenCalledTimes(2)

    await pass(5_000)
    expect(getPositions).toHaveBeenCalledTimes(3)
    await pass(30_000)
    expect(getPositions).toHaveBeenCalledTimes(3)
  })

  it('reloads the books for an order placed from the app, before any broker update', async () => {
    renderScalping()
    await pass(0)
    act(() => socket.fire('order_event', { action: 'BUY', symbol: CALL, orderid: '2' }))
    await pass(1_000)
    expect(getOrders).toHaveBeenCalledTimes(2)
  })

  it('reloads nothing while the page is hidden', async () => {
    renderScalping()
    await pass(0)
    setVisibility('hidden')
    fill()
    fill()
    await pass(10_000)
    expect(getPositions).toHaveBeenCalledTimes(1)
  })

  it('reads the MultiQuotes snapshot for a new symbol set at most once in 5 s, never on a timer', async () => {
    renderScalping()
    await pass(0)
    // The underlying first, then the position book's instrument with it: one more read, 5 s on.
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)
    await pass(5_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
    await pass(5 * 60_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })

  it('reads the MultiQuotes snapshot once on coming back after more than 15 s away', async () => {
    renderScalping()
    await pass(10_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)

    act(() => setVisibility('hidden'))
    await pass(10_000)
    act(() => setVisibility('visible'))
    await pass(10_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)

    act(() => setVisibility('hidden'))
    await pass(20_000)
    act(() => setVisibility('visible'))
    await pass(10_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(3)
  })
})
