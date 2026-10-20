/**
 * Holdings, live (M6.4): the holdings are read again only for an update that
 * can change them (an equity delivery fill or rejection), not for every F&O
 * fill of the day; the value moves with the feed, and REST prices are read
 * once, never on a timer; and the "being refreshed" note shows while a read on
 * coming back to the page runs, and only then.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Holding } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getHoldings = vi.fn()
const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getHoldings: (...args: unknown[]) => getHoldings(...args),
    getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args),
  },
}))
vi.mock('@/components/trading', () => ({ PlaceOrderDialog: () => null }))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({ onModeChange: () => () => {} }))
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
import { render, screen } from '@/test/test-utils'
import Holdings from './Holdings'

const SBIN: Holding = {
  symbol: 'SBIN',
  exchange: 'NSE',
  product: 'CNC',
  quantity: 10,
  average_price: 800,
  ltp: 820,
  pnl: 200,
  pnlpercent: 2.5,
} as Holding

const STATS = {
  totalholdingvalue: 8200,
  totalinvvalue: 8000,
  totalprofitandloss: 200,
  totalpnlpercentage: 2.5,
}

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
  feed.reset()
  socket.clear()
  getHoldings
    .mockReset()
    .mockResolvedValue({ status: 'success', data: { holdings: [SBIN], statistics: STATS } })
  getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('Holdings, live', () => {
  it('reads again for an equity delivery fill, not for an F&O fill', async () => {
    render(<Holdings />)
    await pass(0)
    expect(getHoldings).toHaveBeenCalledTimes(1)

    act(() =>
      socket.fire('order_update', {
        orderid: '1',
        exchange: 'NFO',
        product: 'NRML',
        order_status: 'complete',
        filled_quantity: 65,
      })
    )
    act(() => socket.fire('order_event', {}))
    await pass(5_000)
    expect(getHoldings).toHaveBeenCalledTimes(1)

    act(() =>
      socket.fire('order_update', {
        orderid: '2',
        exchange: 'NSE',
        product: 'CNC',
        order_status: 'complete',
        filled_quantity: 10,
      })
    )
    await pass(500)
    expect(getHoldings).toHaveBeenCalledTimes(2)
  })

  it('moves the value with the feed and reads REST prices only once', async () => {
    render(<Holdings />)
    await pass(0)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    act(() => feed.push('SBIN', 'NSE', { ltp: 825 }))
    expect(screen.getAllByText('₹825.00').length).toBeGreaterThan(0)
    await pass(5 * 60_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)
  })

  it('says it is refreshing while a read on coming back runs, and only then', async () => {
    let answer: (value: unknown) => void = () => {}
    render(<Holdings />)
    await pass(0)
    expect(screen.queryByText(/being refreshed/)).toBeNull()

    // A glance away: nothing to read, nothing to say.
    setVisibility('hidden')
    await pass(5_000)
    setVisibility('visible')
    await pass(0)
    expect(screen.queryByText(/being refreshed/)).toBeNull()

    getHoldings.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    setVisibility('hidden')
    await pass(20_000)
    setVisibility('visible')
    await pass(0)
    expect(screen.getByText(/being refreshed/)).toBeInTheDocument()
    await act(async () =>
      answer({ status: 'success', data: { holdings: [SBIN], statistics: STATS } })
    )
    expect(screen.queryByText(/being refreshed/)).toBeNull()
  })
})
