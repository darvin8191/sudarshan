/**
 * The Option chain in the D look (SUDARSHAN T3a, M1.18): calls and puts, bids
 * and asks, buy and sell in the app's tokens, and each colour said again in a
 * word: the side headings, CE and PE beside the open interest totals, which
 * side the put/call ratio leans to, how wide a spread is, and the quick order
 * buttons named for the leg.
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
  chain: [
    { strike: 25900, ce: leg(CE, 120), pe: { ...leg(PE, 110), oi: 6000, bid: 108, ask: 112.5 } },
  ],
}

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

describe('Option chain colours (T3a)', () => {
  it('says in words what each colour says', async () => {
    render(<OptionChain />)
    await pass(0)
    await pass(0)
    expect(screen.getByText('CALLS')).toBeInTheDocument()
    expect(screen.getByText('PUTS')).toBeInTheDocument()
    // The totals name their side; puts lead here, and the ratio says so.
    expect(screen.getByText('CE')).toBeInTheDocument()
    expect(screen.getByText('PE')).toBeInTheDocument()
    expect(screen.getByText(/Put\/Call Ratio · more put OI/)).toBeInTheDocument()
    // A spread of one rupee is tight; four and a half is wide.
    expect(screen.getByTitle('Spread tight')).toHaveTextContent('1.00 tight')
    expect(screen.getByTitle('Spread wide')).toHaveTextContent('4.50 wide')
    // The quick buttons say which leg they buy or sell.
    expect(screen.getByRole('button', { name: `Buy ${CE}` })).toHaveTextContent('B')
    expect(screen.getByRole('button', { name: `Sell ${PE}` })).toHaveTextContent('S')
  })

  it('draws nothing in a red or green of its own', async () => {
    render(<OptionChain />)
    await pass(0)
    await pass(0)
    const raw = [...document.querySelectorAll('[class]')].filter((el) =>
      /\b(?:text|bg|border|from|to)-(?:red|green|emerald|rose|lime)-\d/.test(
        el.getAttribute('class') ?? ''
      )
    )
    expect(raw.map((el) => el.getAttribute('class'))).toEqual([])
  })
})
