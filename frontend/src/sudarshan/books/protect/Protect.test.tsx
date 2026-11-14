/**
 * Protect on a holding (M5.6, G1 and G2), through the real Holdings page at 390px: shown only
 * where its switch says, one single sell GTT for delivery, asked first, sent once, one per
 * holding, with Change, Remove and the question before Sell.
 */
import { act, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
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
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
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
  OPEN_GUARD.ms = 0
  noteProtectStage('off')
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openSheet(tap: ReturnType<typeof user>, name: RegExp) {
  await tap.click(card(name))
  await settle()
}

async function fillAndAsk(tap: ReturnType<typeof user>, priceTyped: string) {
  await tap.click(screen.getByRole('button', { name: 'Protect' }))
  await settle()
  fireEvent.change(screen.getByLabelText('Sell at price'), { target: { value: priceTyped } })
  await tap.click(screen.getByRole('button', { name: 'Set Protect' }))
  await settle()
}

describe('Protect on a holding (M5.6)', () => {
  it('is nowhere while the switch is off, and the GTT book is not read', async () => {
    noteProtectStage('off')
    await renderPhone(RELIANCE)
    await openSheet(user(), /RELIANCE/)
    expect(screen.queryByRole('button', { name: /Protect/ })).toBeNull()
    expect(api.getGttOrderbook).not.toHaveBeenCalled()
  })

  it('at stage sandbox shows only in Analyze mode', async () => {
    noteProtectStage('sandbox')
    const view = await renderPhone(RELIANCE)
    await openSheet(user(), /RELIANCE/)
    expect(screen.queryByRole('button', { name: /Protect/ })).toBeNull()
    view.unmount()
    theme.appMode = 'analyzer'
    await renderPhone(RELIANCE)
    await openSheet(user(), /RELIANCE/)
    expect(screen.getByRole('button', { name: /Protect/ })).toHaveTextContent('in the sandbox')
  })

  it('sets one: the question first, then one request, a single sell for delivery', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await fillAndAsk(tap, '1420')
    expect(screen.getByTestId('protect-distance')).toHaveTextContent(
      'Sells if the price falls to 1,420.00: -32.30 (-2.22%) from the last price'
    )
    expect(api.post).not.toHaveBeenCalled() // it asks first
    expect(screen.getByText('Sell 40 RELIANCE if the price falls to 1,420.00?')).toBeInTheDocument()
    // the check just before the send finds none; the read after it finds the new one
    api.getGttOrderbook.mockResolvedValueOnce(bookOf()).mockResolvedValue(bookOf(gtt()))
    await tap.click(screen.getByRole('button', { name: 'Set Protect' }))
    await settle()
    await settle()
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post.mock.calls[0].slice(0, 2)).toEqual([
      '/placegttorder',
      {
        apikey: 'test-api-key',
        strategy: 'Protect',
        trigger_type: 'SINGLE',
        exchange: 'NSE',
        symbol: 'RELIANCE',
        action: 'SELL',
        product: 'CNC',
        quantity: 40,
        pricetype: 'LIMIT',
        price: 1420,
        triggerprice_sl: 1420,
        triggerprice_tg: 0,
      },
    ])
    expect(toast.said).toEqual(['ok: Protect set on RELIANCE.'])
    // the card and the sheet now say so, with Change and Remove in place of Protect
    expect(screen.getAllByText('Protected: sell 40 at 1,420.00').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Protect' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Change' })).toBeInTheDocument()
  })

  it('barrier 9: taps inside the first half second do nothing, and a double tap sends once', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await fillAndAsk(tap, '1420')
    let release: (value: unknown) => void = () => {}
    api.post.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      })
    )
    OPEN_GUARD.ms = 500
    // the question is open again from now: a tap that lands on it at once is ignored
    await tap.click(screen.getByRole('button', { name: 'Cancel' }))
    await settle()
    await tap.click(screen.getByRole('button', { name: 'Set Protect' }))
    await settle()
    const confirm = () =>
      within(screen.getByRole('dialog', { name: 'Set Protect?' })).getByRole('button', {
        name: 'Set Protect',
      })
    await tap.click(confirm())
    expect(api.post).not.toHaveBeenCalled()
    await act(async () => void vi.advanceTimersByTime(600))
    await tap.dblClick(confirm())
    await settle()
    expect(api.post).toHaveBeenCalledTimes(1)
    // while it is on its way the button is held, so a third tap has nothing to press
    expect(screen.getByRole('button', { name: 'Protect sent' })).toBeDisabled()
    await act(async () => release({ data: { status: 'success' } }))
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('never sends a second one: the book is read again just before, and one there refuses', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await fillAndAsk(tap, '1420')
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt({ trigger_id: 'GTT-7' }))) // set elsewhere meanwhile
    await tap.click(screen.getByRole('button', { name: 'Set Protect' }))
    await settle()
    await settle()
    expect(api.post).not.toHaveBeenCalled()
    expect(toast.said).toEqual([
      'refused: Protect was not set. RELIANCE already has a Protect order. Change it or remove it first.',
    ])
  })

  it('sends nothing when the GTT book cannot be read to check', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await fillAndAsk(tap, '1420')
    api.getGttOrderbook.mockRejectedValue(new Error('down'))
    await tap.click(screen.getByRole('button', { name: 'Set Protect' }))
    await settle()
    await settle()
    expect(api.post).not.toHaveBeenCalled()
    expect(toast.said[0]).toMatch(/^refused: Protect was not set: the GTT book could not be read/)
  })

  it('will not take a price at or beside the last price, where it could sell at once', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await tap.click(screen.getByRole('button', { name: 'Protect' }))
    await settle()
    fireEvent.change(screen.getByLabelText('Sell at price'), { target: { value: '1450' } })
    expect(screen.getByRole('alert')).toHaveTextContent('within 0.5% of the last price')
    expect(screen.getByRole('button', { name: 'Set Protect' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Sell at price'), { target: { value: '1420' } })
    fireEvent.change(screen.getByLabelText('Shares'), { target: { value: '41' } })
    expect(screen.getByRole('alert')).toHaveTextContent('You hold 40')
    expect(screen.getByRole('button', { name: 'Set Protect' })).toBeDisabled()
  })

  it('reads an answer that may have reached Upstox as not confirmed, and holds', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await fillAndAsk(tap, '1420')
    api.post.mockRejectedValue(new AxiosError('timeout', 'ECONNABORTED'))
    await tap.click(screen.getByRole('button', { name: 'Set Protect' }))
    await settle()
    await settle()
    expect(toast.said[0]).toMatch(/^held: No answer came for this Protect/)
    expect(screen.getByRole('button', { name: 'Protect sent' })).toBeDisabled()
  })

  it('removes one after asking, with the GTT tab’s own cancel call, once', async () => {
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    const tap = user()
    await renderPhone(RELIANCE)
    expect(within(card(/RELIANCE/)).getByText('Protected: sell 40 at 1,420.00')).toBeInTheDocument()
    await openSheet(tap, /RELIANCE/)
    await tap.click(screen.getByRole('button', { name: 'Remove' }))
    await settle()
    expect(api.cancelGttOrder).not.toHaveBeenCalled()
    api.getGttOrderbook.mockResolvedValue(bookOf())
    await tap.dblClick(
      within(screen.getByRole('dialog', { name: 'Remove Protect?' })).getByRole('button', {
        name: 'Remove',
      })
    )
    await settle()
    await settle()
    expect(api.cancelGttOrder.mock.calls).toEqual([['GTT-1']])
    expect(screen.queryByText('Protected: sell 40 at 1,420.00')).toBeNull()
  })

  it('changes one with the same single sell', async () => {
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await tap.click(screen.getByRole('button', { name: 'Change' }))
    await settle()
    fireEvent.change(screen.getByLabelText('Sell at price'), { target: { value: '1400' } })
    await tap.click(screen.getByRole('button', { name: 'Change Protect' }))
    await settle()
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Change Protect?' })).getByRole('button', {
        name: 'Change',
      })
    )
    await settle()
    expect(api.post).not.toHaveBeenCalled()
    expect(api.modifyGttOrder.mock.calls).toEqual([
      [
        'GTT-1',
        {
          strategy: 'Protect',
          trigger_type: 'SINGLE',
          exchange: 'NSE',
          symbol: 'RELIANCE',
          action: 'SELL',
          product: 'CNC',
          quantity: 40,
          pricetype: 'LIMIT',
          price: 1400,
          triggerprice_sl: 1400,
          triggerprice_tg: 0,
        },
      ],
    ])
  })

  it('Sell on a protected holding asks first; ticked, Protect is removed and the ticket opens', async () => {
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await tap.click(screen.getByRole('button', { name: 'Sell' }))
    await settle()
    expect(ticket.opened).toEqual([])
    expect(
      screen.getByText('A Protect order for 40 shares is active. Remove it too?')
    ).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Remove Protect now' })).toBeChecked()
    await tap.click(screen.getByRole('button', { name: 'Continue to sell' }))
    await settle()
    expect(api.cancelGttOrder.mock.calls).toEqual([['GTT-1']])
    expect(ticket.opened[0]).toContain('SELL RELIANCE NSE qty 40')
  })

  it('Sell with the tick taken off leaves Protect in place and says what that means', async () => {
    api.getGttOrderbook.mockResolvedValue(bookOf(gtt()))
    const tap = user()
    await renderPhone(RELIANCE)
    await openSheet(tap, /RELIANCE/)
    await tap.click(screen.getByRole('button', { name: 'Sell' }))
    await settle()
    await tap.click(screen.getByRole('checkbox', { name: 'Remove Protect now' }))
    expect(screen.getByText(/it will try to sell these shares again/)).toBeInTheDocument()
    await tap.click(screen.getByRole('button', { name: 'Continue to sell' }))
    await settle()
    expect(api.cancelGttOrder).not.toHaveBeenCalled()
    expect(ticket.opened[0]).toContain('SELL RELIANCE')
  })

  it('is never offered on a futures or options row (mutation 15)', async () => {
    await renderPhone(OPTION)
    await openSheet(user(), /NIFTY/)
    expect(screen.queryByRole('button', { name: /Protect/ })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Protect' })).toBeNull()
  })
})
