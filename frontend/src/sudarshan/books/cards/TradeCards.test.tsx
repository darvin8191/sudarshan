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
const auth = vi.hoisted(() => ({
  facts: {
    apiKey: 'test-api-key' as string | null,
    sessionChecked: true,
    brokerSessionExpired: false,
    user: { broker: 'upstox' },
  },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: Object.assign(() => auth.facts, { getState: () => auth.facts }),
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

  it('groups the fills of one order on one card: count, quantity, average, each fill (B3)', async () => {
    const part = (quantity: number, average_price: number, time: string): Trade => ({
      ...BOUGHT,
      orderid: '1009',
      quantity,
      average_price,
      trade_value: quantity * average_price,
      timestamp: `2026-10-20 ${time}`,
    })
    await renderPhone(
      BOUGHT,
      part(30, 150, '10:41:50'),
      part(65, 142, '10:41:49'),
      part(35, 143, '10:41:51')
    )
    const cards = within(screen.getByRole('list', { name: 'Trades' })).getAllByRole('link')
    expect(cards).toHaveLength(2)
    expect(cards[0]).toHaveTextContent('10:41:51')
    expect(cards[0]).toHaveTextContent('130 at avg 144.12')
    expect(cards[0]).toHaveTextContent('3 fills · Value ₹18,735.00')
    const fills = within(cards[0]).getByTestId('fills').children
    expect([...fills].map((line) => line.textContent)).toEqual([
      '65 at 142.0010:41:49',
      '30 at 150.0010:41:50',
      '35 at 143.0010:41:51',
    ])
    expect(cards[1]).toHaveTextContent('65 at 142.50')
    expect(within(cards[1]).queryByTestId('fills')).toBeNull()
    expect(screen.getByText(/4 trades in 2 orders · 4 buy · 0 sell/)).toBeInTheDocument()
  })

  it('exports the trade book from the app’s own route, never following its redirect (B3)', async () => {
    const tap = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const fetch = vi.fn(async () => ({ ok: false, status: 0, headers: { get: () => null } }))
    vi.stubGlobal('fetch', fetch)
    await renderPhone(SOLD)
    await tap.click(screen.getByRole('button', { name: 'Export trade book' }))
    expect(fetch).toHaveBeenCalledWith(
      '/tradebook/export',
      expect.objectContaining({ redirect: 'manual' })
    )
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

describe('Trade book without prices', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('offline')))
    )
  })
  afterEach(() => {
    auth.facts = {
      ...auth.facts,
      apiKey: 'test-api-key',
      sessionChecked: true,
      brokerSessionExpired: false,
    }
  })

  it('still checking: a quiet line, never the API key message', async () => {
    auth.facts = { ...auth.facts, apiKey: null, sessionChecked: false }
    await renderPhone()
    expect(screen.getByText('Checking your login')).toBeInTheDocument()
    expect(screen.queryByText(/API key/)).toBeNull()
  })

  it('Upstox not logged in today: says so, with Auto login and Dashboard', async () => {
    auth.facts = { ...auth.facts, apiKey: null, brokerSessionExpired: true }
    await renderPhone()
    expect(screen.getByText(/Upstox is not logged in today/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Auto login' })).toBeInTheDocument()
    expect(screen.queryByText(/API key/)).toBeNull()
  })

  it('no API key: the existing message', async () => {
    auth.facts = { ...auth.facts, apiKey: null }
    await renderPhone()
    expect(screen.getByText('Prices and search need an API key.')).toBeInTheDocument()
  })

  it('ready: no notice', async () => {
    await renderPhone()
    expect(document.querySelector('[data-access]')).toBeNull()
  })
})
