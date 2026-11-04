/**
 * The Kill switch button and sheet on Positions (M20.7): where the button is, and that the
 * phone's Back closes the sheet before it leaves the page (the sheet lives in the address).
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Position } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getPositions: vi.fn(),
  getOrders: vi.fn(),
  getMultiQuotes: vi.fn(),
  closePosition: vi.fn(),
  closeAllPositions: vi.fn(),
  contractOf: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => api.getPositions(...args),
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    closePosition: (...args: unknown[]) => api.closePosition(...args),
    closeAllPositions: (...args: unknown[]) => api.closeAllPositions(...args),
  },
}))
const risk = vi.hoisted(() => ({ preview: vi.fn(), turnOn: vi.fn() }))
vi.mock('@/api/risk', () => ({ riskApi: risk }))
vi.mock('@/sudarshan/watchlist/api', () => ({
  contractOf: (...args: unknown[]) => api.contractOf(...args),
}))
vi.mock('@/stores/authStore', () => {
  const state = { apiKey: 'test-api-key', user: { broker: 'upstox' } }
  const useAuthStore = (pick?: (s: typeof state) => unknown) => (pick ? pick(state) : state)
  return { useAuthStore }
})
const theme = vi.hoisted(() => ({ appMode: 'live' }))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick(theme),
}))
vi.mock('@/utils/toast', () => ({
  showToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
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
vi.mock('@/components/trading/PlaceOrderDialog', () => ({ PlaceOrderDialog: () => null }))

import Positions from '@/pages/Positions'
import { resetSentForTests } from '@/sudarshan/books/sent'
import { noteKillSwitch, resetKillSwitchForTests } from '@/sudarshan/risk/killSwitch'
import { back, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'

vi.setConfig({ testTimeout: 15_000 })

const LONG = {
  symbol: 'NIFTY27OCT2625900CE',
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 142.5,
  ltp: 152.5,
  pnl: 650,
  pnlpercent: 7.02,
  realised: 0,
  unrealised: 650,
} as Position

beforeEach(() => {
  fakeClock()
  resetSentForTests()
  resetKillSwitchForTests()
  frames.install()
  theme.appMode = 'live'
  api.getPositions.mockResolvedValue({ status: 'success', data: [LONG] })
  api.getOrders.mockResolvedValue({ status: 'success', data: { orders: [] } })
  api.getMultiQuotes.mockResolvedValue({ status: 'success', data: [] })
  risk.preview.mockResolvedValue({ counts: null, message: 'counted' })
})

async function open() {
  fakeScreen('phone')
  renderBook('/positions', <Positions />)
  await settle()
}

describe('the Kill switch button on Positions', () => {
  it('is offered where the guard acts on the book the page shows', async () => {
    noteKillSwitch({ known: true, on: false }, 'on')
    await open()
    expect(screen.getByRole('button', { name: 'Kill switch' })).toBeInTheDocument()
  })

  it('is not offered on a Live page while the guard covers the sandbox only', async () => {
    noteKillSwitch({ known: true, on: false }, 'sandbox')
    await open()
    expect(screen.queryByRole('button', { name: 'Kill switch' })).toBeNull()
  })

  it('is offered in Analyze mode while the guard covers the sandbox', async () => {
    theme.appMode = 'analyzer'
    noteKillSwitch({ known: true, on: false }, 'sandbox')
    await open()
    expect(screen.getByRole('button', { name: 'Kill switch' })).toBeInTheDocument()
  })

  it('is not offered while the guard is off on the server', async () => {
    noteKillSwitch({ known: true, on: false }, 'off')
    await open()
    expect(screen.queryByRole('button', { name: 'Kill switch' })).toBeNull()
  })
})

describe('the Kill switch sheet on Positions', () => {
  it("is closed by the phone's Back before the page is left", async () => {
    noteKillSwitch({ known: true, on: false }, 'on')
    await open()
    const tap = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await tap.click(screen.getByRole('button', { name: 'Kill switch' }))
    expect(screen.getByRole('dialog', { name: 'Turn kill switch on' })).toBeInTheDocument()
    expect(where()).toBe('/positions?act=killswitch')
    back()
    await waitFor(() => expect(where()).toBe('/positions'))
    expect(screen.queryByRole('dialog', { name: 'Turn kill switch on' })).toBeNull()
  })
})
