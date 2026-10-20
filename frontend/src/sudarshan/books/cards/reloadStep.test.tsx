/**
 * A step open when the tab was reloaded (M6.9). Android Chrome reloads a tab
 * it has discarded, and the history entry comes back with its state intact,
 * while every module (and the visit counter in it) is loaded afresh. A trader
 * coming back must find the cards, not a confirmation, a ticket or a dialog he
 * did not just ask for. Each case opens the step on a first load of the real
 * page, then loads the page again on that same entry.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getPositions: vi.fn(),
  getOrders: vi.fn(),
  getMultiQuotes: vi.fn(),
  contractOf: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => api.getPositions(...args),
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    getQuotes: vi.fn().mockResolvedValue({ status: 'success', data: {} }),
    getDepth: vi.fn().mockResolvedValue({ status: 'success', data: { bids: [], asks: [] } }),
    closePosition: vi.fn().mockResolvedValue({ status: 'success' }),
    closeAllPositions: vi.fn().mockResolvedValue({ status: 'success' }),
    cancelOrder: vi.fn().mockResolvedValue({ status: 'success' }),
    cancelAllOrders: vi.fn().mockResolvedValue({ status: 'success' }),
    modifyOrder: vi.fn().mockResolvedValue({ status: 'success' }),
  },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  contractOf: (...args: unknown[]) => api.contractOf(...args),
}))
vi.mock('@/components/trading/GttTab', async () => {
  const { createElement } = await import('react')
  return { default: () => createElement('p', null, 'GTT book') }
})
vi.mock('@/components/trading/PlaceOrderDialog', async () => {
  const { createElement } = await import('react')
  return {
    PlaceOrderDialog: (props: Record<string, unknown>) =>
      props.open ? createElement('section', { 'aria-label': 'Order ticket' }, 'ticket') : null,
  }
})
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick({ appMode: 'live' }),
}))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
vi.mock('@/utils/toast', () => ({
  showToast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
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
  useSocketContext: () => ({
    playAlertSound: () => {},
    socket: { on: () => undefined, off: () => undefined },
  }),
}))

import { entryHere, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'

vi.setConfig({ testTimeout: 20_000 })

const CALL = 'NIFTY27OCT2625900CE'
const POSITION = {
  symbol: CALL,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 142.5,
  ltp: 152.5,
  pnl: 650,
  pnlpercent: 7.02,
}
const ORDER = {
  symbol: CALL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 168,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'MIS',
  orderid: '1001',
  order_status: 'open',
  timestamp: '2026-10-20 10:41:51',
}

type Book = 'positions' | 'orderbook'

/** The page as a fresh load of the app has it: every module evaluated again. */
async function freshPage(book: Book): Promise<ReactElement> {
  vi.resetModules()
  if (book === 'positions') {
    const { default: Positions } = await import('@/pages/Positions')
    return <Positions />
  }
  const { default: OrderBook } = await import('@/pages/OrderBook')
  return <OrderBook />
}

function user() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
}

const sheet = () => screen.getByRole('dialog')
const card = (book: Book) =>
  book === 'positions'
    ? screen.getByRole('button', { name: /NIFTY 25900 CE/ })
    : within(document.querySelector('li[data-order="1001"]') as HTMLElement).getByRole('button')

/** How each step is opened, by taps, on the first load. */
const STEPS: Record<string, { book: Book; open: (tap: ReturnType<typeof user>) => Promise<void> }> =
  {
    'the exit-all confirmation': {
      book: 'positions',
      open: async (tap) => tap.click(screen.getByRole('button', { name: 'Exit all' })),
    },
    'an exit confirmation': {
      book: 'positions',
      open: async (tap) => {
        await tap.click(card('positions'))
        await tap.click(within(sheet()).getByRole('button', { name: /^Exit/ }))
      },
    },
    'the Add ticket': {
      book: 'positions',
      open: async (tap) => {
        await tap.click(card('positions'))
        const add = within(sheet()).getByRole('button', { name: /^Add/ })
        await waitFor(() => expect(add).toBeEnabled())
        await tap.click(add)
      },
    },
    'the cancel-all confirmation': {
      book: 'orderbook',
      open: async (tap) => tap.click(screen.getByRole('button', { name: /^Cancel all/ })),
    },
    'a cancel confirmation': {
      book: 'orderbook',
      open: async (tap) => {
        await tap.click(card('orderbook'))
        await tap.click(within(sheet()).getByRole('button', { name: 'Cancel order' }))
      },
    },
    'the modify dialog': {
      book: 'orderbook',
      open: async (tap) => {
        await tap.click(card('orderbook'))
        await tap.click(within(sheet()).getByRole('button', { name: 'Modify' }))
      },
    },
  }

function nothingOpen() {
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull()
}

beforeEach(() => {
  fakeClock()
  frames.install()
  fakeScreen('phone')
  api.getPositions.mockReset().mockResolvedValue({ status: 'success', data: [POSITION] })
  api.getOrders.mockReset().mockResolvedValue({
    status: 'success',
    data: { orders: [ORDER], statistics: {} },
  })
  api.getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  api.contractOf.mockReset().mockResolvedValue({ lotSize: 65, tickSize: 0.05 })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('a reload with a step open', () => {
  it('does not show a confirmation from an entry an earlier load wrote (the review probe)', async () => {
    const page = await freshPage('positions')
    renderBook('/positions', page, [
      '/dashboard',
      { pathname: '/positions', search: '?act=exitall', state: { sdOverlay: true, sdVisit: 'v1' } },
    ])
    await settle()
    expect(screen.queryByRole('dialog', { name: 'Exit all positions?' })).toBeNull()
    await waitFor(() => expect(where()).toBe('/positions'))
  })

  it.each(Object.keys(STEPS))('does not open %s again by itself', async (name) => {
    const { book, open } = STEPS[name]
    const path = `/${book}`
    const tap = user()

    const first = renderBook(path, await freshPage(book))
    await settle()
    await open(tap)
    expect(where()).toContain('act=')
    const restored = entryHere()
    first.unmount()

    renderBook(path, await freshPage(book), ['/dashboard', restored])
    await settle()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    nothingOpen()
    await waitFor(() => expect(where()).toBe(path))
    expect(card(book)).toBeInTheDocument()
  })
})
