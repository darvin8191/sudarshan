/**
 * The PnL Tracker, live (M6.5): the current MTM is the position book's own
 * P&L moved by every tick, the book is read again when the broker pushes a
 * fill, prices come from the feed and never from a timer, and the page says
 * in plain words where each figure comes from: the current MTM from the
 * broker's positions, the rest from today's trades as at the last Refresh.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

// jsdom has no canvas: a chart that accepts every call and draws nothing.
vi.mock('lightweight-charts', () => {
  const inert = (): unknown =>
    new Proxy(() => {}, {
      get: (_target, prop) => {
        if (prop === 'then') return undefined
        if (prop === Symbol.toPrimitive || prop === 'valueOf') return () => 0
        if (prop === 'toString') return () => ''
        return inert()
      },
      apply: () => inert(),
    })
  return {
    createChart: () => inert(),
    BaselineSeries: {},
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
  }
})
vi.mock('html2canvas-pro', () => ({ default: vi.fn() }))

const getPositions = vi.fn()
const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => getPositions(...args),
    getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args),
  },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: () => ({ mode: 'light' }),
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
import { render, screen } from '@/test/test-utils'
import PnLTracker from './PnLTracker'

const CALL = 'NIFTY28OCT2525900CE'
const POSITION = {
  symbol: CALL,
  exchange: 'NFO',
  product: 'NRML',
  quantity: 65,
  average_price: 120,
  ltp: 130,
  pnl: 650,
}

const TRACKER = {
  status: 'success',
  data: {
    current_mtm: 640,
    max_mtm: 900,
    max_mtm_time: '10:05',
    min_mtm: -200,
    min_mtm_time: '09:20',
    max_drawdown: -300,
    pnl_series: [],
    drawdown_series: [],
  },
}

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(Date.UTC(2026, 9, 20, 5, 0, 0))
  feed.reset()
  socket.clear()
  getPositions.mockReset().mockResolvedValue({ status: 'success', data: [POSITION] })
  getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => (url === '/auth/csrf-token' ? { csrf_token: 'token' } : TRACKER),
    }))
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('PnL Tracker, live', () => {
  it('moves the current MTM from the broker book with every tick, and asks no REST prices', async () => {
    render(<PnLTracker />)
    await pass(0)
    expect(screen.getByTestId('current-mtm')).toHaveTextContent('₹650.00')

    await pass(1_000)
    act(() => feed.push(CALL, 'NFO', { ltp: 134 }))
    expect(screen.getByTestId('current-mtm')).toHaveTextContent('₹910.00')
    await pass(5 * 60_000)
    expect(getMultiQuotes).not.toHaveBeenCalled()
  })

  it('reads the position book again when the broker pushes a fill', async () => {
    render(<PnLTracker />)
    await pass(0)
    expect(getPositions).toHaveBeenCalledTimes(1)

    act(() => socket.fire('order_update', { orderid: '1', order_status: 'complete' }))
    await pass(500)
    expect(getPositions).toHaveBeenCalledTimes(2)
  })

  it('says where each figure comes from, in plain words', async () => {
    render(<PnLTracker />)
    await pass(0)
    const sources = screen.getByTestId('mtm-sources')
    expect(sources).toHaveTextContent(
      'Current MTM is your open positions as the broker reports them'
    )
    expect(sources).toHaveTextContent(
      "worked out from today's trades and one-minute prices, as at 10:30:00"
    )
  })
})
