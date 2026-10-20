/**
 * The Trade book, live (M6.2): a fill Upstox pushes (order_update) brings the
 * new trade without a manual refresh; before, only an order placed from this
 * app or a sandbox update did. Nothing is read on a timer outside market
 * hours, and nothing while the page is hidden.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getTrades = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getTrades: (...args: unknown[]) => getTrades(...args) },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({ onModeChange: () => () => {} }))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
let marketOpen = false
const calendar = {
  isMarketOpen: () => marketOpen,
  isAnyMarketOpen: () => marketOpen,
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

import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { fakeScreen } from '@/test/screenWidth'
import { render, screen } from '@/test/test-utils'
import TradeBook from './TradeBook'

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
  socket.clear()
  marketOpen = false
  getTrades.mockReset().mockResolvedValue({ status: 'success', data: [] })
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('Trade book, live', () => {
  it('reads the trades again when the broker pushes a fill', async () => {
    render(<TradeBook />)
    await pass(0)
    expect(getTrades).toHaveBeenCalledTimes(1)

    act(() => socket.fire('order_update', { orderid: '1', order_status: 'complete' }))
    await pass(500)
    expect(getTrades).toHaveBeenCalledTimes(2)
  })

  it('reads nothing on a timer while every market is shut, and nothing while hidden', async () => {
    render(<TradeBook />)
    await pass(0)
    await pass(RESYNC_MS * 4)
    expect(getTrades).toHaveBeenCalledTimes(1)

    marketOpen = true
    setVisibility('hidden')
    act(() => socket.fire('order_update', { orderid: '2', order_status: 'complete' }))
    await pass(RESYNC_MS * 2)
    expect(getTrades).toHaveBeenCalledTimes(1)
    setVisibility('visible')
    await pass(0)
    expect(getTrades).toHaveBeenCalledTimes(2)
  })
})

// SUDARSHAN M4.15: on a phone a trade's symbol opens the phone chart; a wide
// screen keeps upstream's link into the terminal.
describe('Trade book chart link', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("opens the phone chart from a trade's symbol on a phone, the terminal on a wide screen", async () => {
    const CALL = 'NIFTY28OCT2525900CE'
    getTrades.mockResolvedValue({
      status: 'success',
      data: [
        {
          symbol: CALL,
          exchange: 'NFO',
          action: 'BUY',
          quantity: 65,
          average_price: 120,
          trade_value: 7800,
          product: 'MIS',
          orderid: '251020000598204',
          timestamp: '09:20:05',
        },
      ],
    })
    const screenNow = fakeScreen('phone')
    render(<TradeBook />)
    await pass(0)
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
