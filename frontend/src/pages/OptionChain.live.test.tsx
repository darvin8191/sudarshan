/**
 * The Option chain, live (M3.7): each leg's LTP moves with its ticks and shows
 * which way the last one went, the chain's status line says where the prices
 * stand, and once the ticks carry open interest and volume nothing is read on
 * a timer (the chain's REST read was kept only for those). The chain is not a
 * book: an order update does not read it again.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
vi.mock('@/components/trading', () => ({ PlaceOrderDialog: () => null }))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: {
    getUnderlyings: vi.fn().mockResolvedValue({ status: 'success', underlyings: ['NIFTY'] }),
    getExpiries: vi.fn().mockResolvedValue({ status: 'success', expiries: ['28-OCT-2025'] }),
  },
}))
/** One object, as the real hook keeps its answers stable between renders. */
const exchanges = vi.hoisted(() => ({
  toolsFnoExchanges: [{ value: 'NFO', label: 'NFO' }],
  defaultToolsFnoExchange: 'NFO',
  defaultUnderlyings: { NFO: ['NIFTY'] },
}))
vi.mock('@/hooks/useSupportedExchanges', () => ({ useSupportedExchanges: () => exchanges }))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
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
import OptionChain from './OptionChain'

const CE = 'NIFTY28OCT2525900CE'
const PE = 'NIFTY28OCT2525900PE'

function leg(symbol: string, ltp: number) {
  return {
    symbol,
    label: 'ATM',
    ltp,
    bid: ltp - 0.5,
    ask: ltp + 0.5,
    bid_qty: 75,
    ask_qty: 75,
    open: ltp,
    high: ltp,
    low: ltp,
    prev_close: ltp,
    volume: 1000,
    oi: 5000,
    lotsize: 65,
    tick_size: 0.05,
  }
}

const CHAIN = {
  status: 'success',
  underlying: 'NIFTY',
  underlying_symbol: 'NIFTY',
  underlying_exchange: 'NSE_INDEX',
  underlying_ltp: 25900,
  underlying_prev_close: 25850,
  expiry_date: '28OCT25',
  atm_strike: 25900,
  chain: [{ strike: 25900, ce: leg(CE, 120), pe: leg(PE, 110) }],
}

const chainReads = () =>
  vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === '/api/v1/optionchain').length

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
  socket.clear()
  localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(CHAIN)))
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Option chain, live', () => {
  it('moves each leg with its ticks, and reads nothing on a timer once they carry OI and volume', async () => {
    render(<OptionChain />)
    await pass(0)
    await pass(0)
    expect(chainReads()).toBe(1)

    act(() => {
      feed.push(CE, 'NFO', { ltp: 121.5, oi: 5200, volume: 1500 } as never)
      feed.push(PE, 'NFO', { ltp: 108, oi: 4800, volume: 1300 } as never)
    })
    expect(document.querySelector('[data-tick="up"]')).not.toBeNull()
    expect(document.querySelector('[data-tick="down"]')).not.toBeNull()
    expect(screen.getByText('Real-time')).toBeInTheDocument()

    await pass(90_000)
    expect(chainReads()).toBe(1)
  })

  it('does not read the chain again for an order update', async () => {
    render(<OptionChain />)
    await pass(0)
    await pass(0)
    act(() => socket.fire('order_update', { orderid: '1', order_status: 'complete' }))
    await pass(5_000)
    expect(chainReads()).toBe(1)
  })
})
