/**
 * The Positions page, live (M6.3, M3.7): the broker's own fills re-read the
 * book, the P&L moves from the broker's figure with every tick, the LTP shows
 * its last tick's direction, and the status line says where the prices stand:
 * "Real-time" while ticks arrive, "Stale" with greyed figures (sign and arrow
 * kept) once they stop for 10 seconds in market hours. The real useLivePrice
 * and useMarketData run over the market-data harness.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Position } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getPositions = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => getPositions(...args),
    getMultiQuotes: vi.fn().mockResolvedValue({ status: 'success', results: [] }),
    closePosition: vi.fn(),
    closeAllPositions: vi.fn(),
  },
}))

vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({ onModeChange: () => () => {} }))
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
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

import { feed } from '@/test/marketDataHarness'
import { render, screen, within } from '@/test/test-utils'
import Positions from './Positions'

const CALL = 'NIFTY28OCT2525900CE'

/** Bought 130 at 120, sold 65 at 140: +1,300 booked; 65 held at ltp 130. */
const PARTLY_BOOKED: Position = {
  symbol: CALL,
  exchange: 'NFO',
  product: 'NRML',
  quantity: 65,
  average_price: 120,
  ltp: 130,
  pnl: 1950,
} as Position

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function row() {
  return screen.getByText(CALL).closest('tr') as HTMLElement
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
  localStorage.clear()
  getPositions.mockReset().mockResolvedValue({ status: 'success', data: [PARTLY_BOOKED] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Positions, live', () => {
  it('re-reads the book when the broker reports a fill, a rejection or a square-off', async () => {
    render(<Positions />)
    await pass(0)
    expect(getPositions).toHaveBeenCalledTimes(1)

    act(() => socket.fire('order_update', { orderid: '1', order_status: 'complete' }))
    await pass(500)
    expect(getPositions).toHaveBeenCalledTimes(2)
  })

  it('moves the P&L from the broker figure, keeping the booked profit, with the tick direction', async () => {
    render(<Positions />)
    await pass(1_000)

    act(() => feed.push(CALL, 'NFO', { ltp: 134 }))
    const cells = within(row()).getAllByRole('cell')
    // LTP, then P&L: 1,950 + (134 - 130) x 65 = 2,210, not upstream's 910.
    expect(cells[5]).toHaveTextContent('₹134.00')
    expect(cells[5].querySelector('[data-tick="up"]')).not.toBeNull()
    expect(cells[6]).toHaveTextContent('₹2,210.00')
    expect(screen.getByText('Real-time')).toBeInTheDocument()
    expect(screen.queryByText('Live')).toBeNull()
  })

  it('greys the figures and says Stale when ticks stop for 10 seconds in market hours', async () => {
    render(<Positions />)
    await pass(1_000)
    act(() => feed.push(CALL, 'NFO', { ltp: 129 }))
    expect(within(row()).getAllByRole('cell')[5].querySelector('[data-tick="down"]')).not.toBeNull()

    await pass(12_000)
    const cells = within(row()).getAllByRole('cell')
    expect(screen.getByText(/Stale · updated 12s ago/)).toBeInTheDocument()
    expect(cells[5].querySelector('[data-stale="true"]')).not.toBeNull()
    // Greyed, but the triangle and the figure stay.
    expect(cells[5].querySelector('[data-tick="down"]')).not.toBeNull()
    expect(cells[6]).toHaveClass('text-muted-foreground')
    expect(cells[6]).toHaveTextContent('₹1,885.00')
  })

  it('says Reconnecting while the market-data socket is down, never Real-time', async () => {
    render(<Positions />)
    await pass(1_000)
    act(() => feed.push(CALL, 'NFO', { ltp: 131 }))
    act(() => feed.setState({ isAuthenticated: false, connectionState: 'connecting' }))
    await pass(1_000)

    expect(screen.getByText(/Reconnecting/)).toBeInTheDocument()
    expect(screen.queryByText('Real-time')).toBeNull()
  })
})
