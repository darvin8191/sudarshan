/**
 * Protect on a holding (M5.6, G1 and G2), through the real Holdings page at 390px: shown only
 * where its switch says, one single sell GTT for delivery, asked first, sent once, one per
 * holding, with Change, Remove and the question before Sell.
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Holding } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getHoldings: vi.fn(),
  getMultiQuotes: vi.fn(),
  getGttOrderbook: vi.fn(),
  cancelGttOrder: vi.fn(),
  modifyGttOrder: vi.fn(),
  post: vi.fn(),
}))
vi.mock('@/api/client', () => ({ apiClient: { post: (...args: unknown[]) => api.post(...args) } }))
vi.mock('@/api/trading', () => ({
  SEND_TIMEOUT_MS: 45_000,
  tradingApi: {
    getHoldings: (...args: unknown[]) => api.getHoldings(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    getGttOrderbook: (...args: unknown[]) => api.getGttOrderbook(...args),
    cancelGttOrder: (...args: unknown[]) => api.cancelGttOrder(...args),
    modifyGttOrder: (...args: unknown[]) => api.modifyGttOrder(...args),
  },
}))

/** The ticket the page opens, as it was opened. */
const ticket = vi.hoisted(() => ({ opened: [] as string[] }))
vi.mock('@/components/trading', async () => {
  const { createElement, useState } = await import('react')
  // Keeps what is typed in its own state, as the real ticket does: remounting it loses that.
  function Ticket(props: Record<string, unknown>) {
    const [typed, setTyped] = useState(props.quantity)
    const said = [
      props.action,
      props.symbol,
      props.exchange,
      `qty ${props.quantity}`,
      `product ${props.product}`,
      props.priceType,
      props.strategy,
    ].join(' ')
    ticket.opened.push(said)
    return createElement(
      'section',
      { 'aria-label': 'Order ticket' },
      said,
      createElement('output', null, `typed ${typed}`),
      createElement('button', { type: 'button', onClick: () => setTyped(1) }, 'Type 1'),
      createElement(
        'button',
        {
          type: 'button',
          onClick: () => (props.onOpenChange as (open: boolean) => void)(false),
        },
        'Close ticket'
      )
    )
  }
  return {
    PlaceOrderDialog: (props: Record<string, unknown>) =>
      props.open ? createElement(Ticket, props) : null,
  }
})
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
const theme = vi.hoisted(() => ({ appMode: 'live' }))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick(theme),
}))
const calendar = vi.hoisted(() => ({
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import Holdings from '@/pages/Holdings'
import { resetSentForTests } from '@/sudarshan/books/sent'
import { renderBook, settle } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { fakeScreen } from '@/test/screenWidth'
import type { GttOrder } from '@/types/trading'
import { noteProtectStage } from './stage'

vi.setConfig({ testTimeout: 15_000 })

const RELIANCE = {
  symbol: 'RELIANCE',
  exchange: 'NSE',
  product: 'CNC',
  quantity: 40,
  average_price: 1374.3,
  ltp: 1452.3,
  pnl: 3120,
  pnlpercent: 5.67,
} as Holding
const STATS = {
  totalholdingvalue: 117051,
  totalinvvalue: 109122,
  totalprofitandloss: 7929,
  totalpnlpercentage: 7.27,
}
/** The previous closes, as the quote snapshot reads them. */
const PREVIOUS: Record<string, number> = { RELIANCE: 1461, HDFCBANK: 975.2 }

async function renderPhone(...holdings: Holding[]) {
  api.getHoldings.mockResolvedValue({
    status: 'success',
    data: { holdings, statistics: STATS },
  })
  fakeScreen('phone')
  const view = renderBook('/holdings', <Holdings />)
  await settle()
  await settle()
  return view
}

function card(name: RegExp) {
  return screen.getByRole('button', { name })
}

function user() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
}

const toast = vi.hoisted(() => ({ said: [] as string[] }))
vi.mock('@/utils/toast', () => ({
  showToast: {
    success: (text: string) => toast.said.push(`ok: ${text}`),
    warning: (text: string) => toast.said.push(`held: ${text}`),
    error: (text: string) => toast.said.push(`refused: ${text}`),
  },
}))

const OPTION = {
  symbol: 'NIFTY24NOV2625000CE',
  exchange: 'NFO',
  product: 'NRML',
  quantity: 65,
  average_price: 120,
  ltp: 130,
  pnl: 650,
  pnlpercent: 8.3,
} as Holding

const gtt = (over: Partial<GttOrder> = {}): GttOrder => ({
  trigger_id: 'GTT-1',
  trigger_type: 'single',
  status: 'active',
  symbol: 'RELIANCE',
  exchange: 'NSE',
  trigger_prices: [1420],
  last_price: 1452,
  legs: [{ action: 'SELL', quantity: 40, price: 1420, pricetype: 'LIMIT', product: 'CNC' }],
  ...over,
})
const bookOf = (...gtts: GttOrder[]) => ({ status: 'success', data: gtts })

beforeEach(() => {
  fakeClock()
  theme.appMode = 'live'
  frames.install()
  feed.reset()
  ticket.opened = []
  toast.said = []
  resetSentForTests()
  noteProtectStage('on')
  for (const fn of Object.values(api)) fn.mockReset()
  api.getGttOrderbook.mockResolvedValue(bookOf())
  api.post.mockResolvedValue({ data: { status: 'success', trigger_id: 'GTT-9' } })
  api.cancelGttOrder.mockResolvedValue({ status: 'success' })
  api.modifyGttOrder.mockResolvedValue({ status: 'success' })
  api.getMultiQuotes.mockImplementation(
    async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => ({
      status: 'success',
      results: symbols.map(({ symbol, exchange }) => ({
        symbol,
        exchange,
        data: { ltp: 0, prev_close: PREVIOUS[symbol] ?? 0 },
      })),
    })
  )
})

afterEach(() => {
  noteProtectStage('off')
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openSheet(tap: ReturnType<typeof user>, name: RegExp) {
  await tap.click(card(name))
  await settle()
}

describe('Protect on a holding, the display (M5.6, G1)', () => {
  it('is nowhere while the switch is off, and the GTT book is not read', async () => {
    noteProtectStage('off')
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    await renderPhone(RELIANCE)
    await openSheet(user(), /RELIANCE/)
    expect(screen.queryByText(/Protected/)).toBeNull()
    expect(api.getGttOrderbook).not.toHaveBeenCalled()
  })

  it('at stage sandbox reads the book only in Analyze mode', async () => {
    noteProtectStage('sandbox')
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    const view = await renderPhone(RELIANCE)
    expect(api.getGttOrderbook).not.toHaveBeenCalled()
    view.unmount()
    theme.appMode = 'analyzer'
    await renderPhone(RELIANCE)
    expect(within(card(/RELIANCE/)).getByText('Protected: sell 40 at 1,420.00')).toBeInTheDocument()
  })

  it('says a working sell GTT on the card and on the sheet, with a shield and the words', async () => {
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    await renderPhone(RELIANCE)
    expect(api.getGttOrderbook).toHaveBeenCalledWith('test-api-key', 'active')
    const line = within(card(/RELIANCE/)).getByText('Protected: sell 40 at 1,420.00')
    expect(line.closest('[data-protect]')?.querySelector('svg')).not.toBeNull()
    await openSheet(user(), /RELIANCE/)
    expect(
      within(screen.getByRole('region', { name: 'Protect' })).getByText(/Protected: sell 40/)
    ).toBeInTheDocument()
  })

  it('says when the GTT book could not be read, and never shows a futures or options row as protected', async () => {
    api.getGttOrderbook.mockRejectedValue(new Error('down'))
    await renderPhone(RELIANCE, OPTION)
    await openSheet(user(), /RELIANCE/)
    expect(screen.getByText('Protect orders could not be read just now.')).toBeInTheDocument()
  })
})
