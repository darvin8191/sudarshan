/**
 * The Arbitrage page in the D look (SUDARSHAN T3b, M1.18): the connection is
 * "Connected", never "Live", which names the trading mode; a pair whose ticks
 * are arriving says "Real-time" beside its mark; and a spread carries its sign
 * as well as the up or down token it is drawn in.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: (select?: (state: unknown) => unknown) => {
    const state = { apiKey: 'test-api-key', user: { broker: 'upstox' } }
    return select ? select(state) : state
  },
}))
const { NEAR, FAR } = vi.hoisted(() => ({ NEAR: 'NIFTY28OCT25FUT', FAR: 'NIFTY25NOV25FUT' }))
vi.mock('@/api/arbitrage', () => ({
  arbitrageApi: {
    getUniverse: vi.fn().mockResolvedValue({
      status: 'success',
      data: {
        pairs: [
          {
            id: 'NIFTY-near-next',
            underlying: 'NIFTY',
            exchange: 'NFO',
            type: 'near-next',
            near: {
              symbol: NEAR,
              exchange: 'NFO',
              expiry: '28-OCT-25',
              lotsize: 65,
              tick_size: 0.1,
            },
            far: { symbol: FAR, exchange: 'NFO', expiry: '25-NOV-25', lotsize: 65, tick_size: 0.1 },
          },
        ],
        symbols: [
          { symbol: NEAR, exchange: 'NFO' },
          { symbol: FAR, exchange: 'NFO' },
        ],
        counts: { underlyings: 1, pairs: 1, symbols: 2 },
        generated_at: '2026-10-20T04:30:00Z',
      },
    }),
  },
}))

import { feed } from '@/test/marketDataHarness'
import { render, screen } from '@/test/test-utils'
import Arbitrage from './Arbitrage'

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Arbitrage colours (T3b)', () => {
  it('says Connected, Real-time for a ticking pair, and signs its spread', async () => {
    render(<Arbitrage />)
    await pass(0)
    await pass(0)
    act(() => feed.setState({ isConnected: true, isAuthenticated: true }))
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.queryByText('Live')).toBeNull()
    act(() => {
      feed.push(NEAR, 'NFO', { ltp: 100.2, bid_price: 100, ask_price: 100.5 } as never)
      feed.push(FAR, 'NFO', { ltp: 102.2, bid_price: 102, ask_price: 102.5 } as never)
    })
    await pass(1000)
    // Sell the far at 102 and buy the near at 100.50: 1.50 on a mid of 100.25.
    expect(screen.getByText('+1.50%')).toBeInTheDocument()
    expect(screen.getByText(/Real-time,/)).toBeInTheDocument()
    expect(screen.getByTitle('Real-time')).toBeInTheDocument()
  })
})
