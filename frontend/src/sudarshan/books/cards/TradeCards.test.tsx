/**
 * The trade book on a phone (M6.9), through the real TradeBook page at 390px:
 * a card per fill, newest first, with its side in words, quantity, price and
 * time, and a tap that opens its chart. On a wide screen the table is upstream's.
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Trade } from '@/types/trading'

const api = vi.hoisted(() => ({ getTrades: vi.fn() }))
vi.mock('@/api/trading', () => ({
  tradingApi: { getTrades: (...args: unknown[]) => api.getTrades(...args) },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({ onModeChange: () => () => {} }))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
    isLoading: false,
    error: null,
  }),
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import TradeBook from '@/pages/TradeBook'
import { renderBook, settle } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'

const CALL = 'NIFTY27OCT2625900CE'

const BOUGHT: Trade = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'BUY',
  quantity: 65,
  average_price: 142.5,
  trade_value: 9262.5,
  product: 'MIS',
  orderid: '1003',
  timestamp: '2026-10-20 10:12:18',
}
const SOLD: Trade = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  average_price: 168.05,
  trade_value: 10923.25,
  product: 'MIS',
  orderid: '1001',
  timestamp: '2026-10-20 10:41:51',
}

async function renderPhone(...trades: Trade[]) {
  api.getTrades.mockResolvedValue({ status: 'success', data: trades })
  fakeScreen('phone')
  renderBook('/tradebook', <TradeBook />)
  await settle()
}

beforeEach(() => {
  fakeClock()
  frames.install()
  api.getTrades.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Trade book on a phone', () => {
  it('shows each fill as a card, newest first: side, name, quantity, price and time', async () => {
    await renderPhone(BOUGHT, SOLD)
    expect(screen.queryByRole('table')).toBeNull()
    const cards = within(screen.getByRole('list', { name: 'Trades' })).getAllByRole('link')
    expect(cards).toHaveLength(2)
    expect(cards[0]).toHaveTextContent('SELL')
    expect(cards[0]).toHaveTextContent('NIFTY 25900 CE')
    expect(cards[0]).toHaveTextContent('10:41:51')
    expect(cards[0]).toHaveTextContent('65 at 168.05')
    expect(cards[0]).toHaveTextContent('NFO · 27 Oct 2026 · MIS')
    expect(cards[0]).toHaveTextContent('Value ₹10,923.25')
    expect(cards[1]).toHaveTextContent('BUY')
    expect(cards[1]).toHaveTextContent('65 at 142.50')
    expect(screen.getByText(/2 trades · 1 buy · 1 sell/)).toBeInTheDocument()
  })

  it('opens the fill’s chart on a tap', async () => {
    const tap = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await renderPhone(SOLD)
    const fill = within(screen.getByRole('list', { name: 'Trades' })).getByRole('link')
    expect(fill).toHaveAttribute('href', `/chart/NFO/${CALL}`)
    await tap.click(fill)
    expect(await screen.findByText(`Chart of ${CALL} on NFO`)).toBeInTheDocument()
  })

  it('says plainly when there are no trades', async () => {
    await renderPhone()
    expect(screen.getByText('No trades today')).toBeInTheDocument()
  })
})

describe('Trade book on a wide screen', () => {
  it('keeps upstream’s table', async () => {
    api.getTrades.mockResolvedValue({ status: 'success', data: [BOUGHT] })
    fakeScreen('wide')
    renderBook('/tradebook', <TradeBook />)
    await settle()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Trade Value' })).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Trades' })).toBeNull()
  })
})
