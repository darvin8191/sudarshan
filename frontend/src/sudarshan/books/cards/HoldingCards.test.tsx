/**
 * Holdings on a phone (M6.9), through the real Holdings page at 390px: the
 * current value, invested, overall P&L and day's change at the top, one card
 * per holding, prices that move with ticks, and a sheet whose Buy more and Sell
 * open the page's own ticket (Sell as its table's Exit does, Buy more from one
 * share). Back closes the ticket. On a wide screen the table is upstream's.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Holding } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({ getHoldings: vi.fn(), getMultiQuotes: vi.fn() }))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getHoldings: (...args: unknown[]) => api.getHoldings(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
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
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
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
import { back, pass, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { fakeScreen } from '@/test/screenWidth'
import { STALE } from './parts'

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
const HDFCBANK = {
  symbol: 'HDFCBANK',
  exchange: 'NSE',
  product: 'CNC',
  quantity: 60,
  average_price: 902.5,
  ltp: 982.65,
  pnl: 4809,
  pnlpercent: 8.88,
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

beforeEach(() => {
  fakeClock()
  theme.appMode = 'live'
  frames.install()
  feed.reset()
  ticket.opened = []
  api.getHoldings.mockReset()
  api.getMultiQuotes
    .mockReset()
    .mockImplementation(
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
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Holdings on a phone', () => {
  it('sums up the holdings: current value, invested, overall P&L and the day’s change', async () => {
    await renderPhone(RELIANCE, HDFCBANK)
    expect(screen.queryByRole('table')).toBeNull()
    const summary = screen.getByRole('region', { name: 'Current value' })
    expect(screen.getByTestId('holdings-value')).toHaveTextContent('₹1,17,051.00')
    expect(summary).toHaveTextContent('Invested₹1,09,122.00')
    expect(summary).toHaveTextContent('Overall P&L+₹7,929.00+7.27%')
    // (1,452.30 - 1,461) x 40 + (982.65 - 975.20) x 60, on 1,16,952 at the previous close.
    expect(summary).toHaveTextContent("Day's change+₹99.00+0.08%")
  })

  it('shows each holding as a card, its figures carried by signs', async () => {
    await renderPhone(RELIANCE, HDFCBANK)
    const reliance = card(/RELIANCE/)
    expect(reliance).toHaveTextContent('Qty 40 · Avg 1,374.30')
    expect(reliance).toHaveTextContent('+₹3,120.00')
    expect(reliance).toHaveTextContent('+5.67%')
    expect(reliance).toHaveTextContent('LTP1,452.30')
    expect(reliance).toHaveTextContent('Day −0.60%')
    expect(reliance).toHaveTextContent('Value ₹58,092.00')
    // The P&L's own arrow points the way its sign does.
    expect(reliance.querySelector('span[data-direction="up"] > svg')).toHaveAttribute(
      'data-direction',
      'up'
    )
  })

  it('moves the value and the P&L with every tick', async () => {
    await renderPhone(RELIANCE, HDFCBANK)
    act(() => {
      feed.push('RELIANCE', 'NSE', { ltp: 1460, cp: 1461 })
      frames.flush()
    })
    const reliance = card(/RELIANCE/)
    // 3,120 + (1,460 - 1,452.30) x 40
    expect(reliance).toHaveTextContent('+₹3,428.00')
    expect(reliance).toHaveTextContent('Value ₹58,400.00')
    expect(reliance.querySelector('[data-tick="up"]')).toHaveTextContent('1,460.00')
    expect(screen.getByTestId('holdings-value')).toHaveTextContent('₹1,17,359.00')
  })

  it('says it waits for the previous close rather than showing a day’s change of zero', async () => {
    api.getMultiQuotes.mockImplementation(
      async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => ({
        status: 'success',
        results: symbols.map(({ symbol, exchange }) => ({ symbol, exchange, data: { ltp: 0 } })),
      })
    )
    await renderPhone(RELIANCE, HDFCBANK)
    const summary = screen.getByRole('region', { name: 'Current value' })
    expect(summary).toHaveTextContent("Day's changeWaiting for the previous close")
    expect(summary).not.toHaveTextContent('₹0.00')
    expect(card(/RELIANCE/)).not.toHaveTextContent('Day')
  })

  it('greys the summary and the holdings whose ticks have stopped, keeping signs and arrows', async () => {
    await renderPhone(RELIANCE, HDFCBANK)
    act(() => {
      feed.push('RELIANCE', 'NSE', { ltp: 1460, cp: 1461 })
      feed.push('HDFCBANK', 'NSE', { ltp: 990, cp: 975.2 })
      frames.flush()
    })
    const value = screen.getByTestId('holdings-value')
    expect(value).not.toHaveClass(STALE)
    await pass(12_000)

    expect(screen.getByTestId('holdings-value')).toHaveClass(STALE)
    const summary = screen.getByRole('region', { name: 'Current value' })
    const overall = summary.querySelector('span[data-direction]') as HTMLElement
    expect(overall).toHaveClass(STALE)
    expect(overall.querySelector(':scope > svg')).toHaveAttribute('data-direction', 'up')

    const reliance = card(/RELIANCE/)
    const pnl = reliance.querySelector('span[data-direction]') as HTMLElement
    expect(pnl).toHaveClass(STALE)
    expect(pnl).not.toHaveClass('text-[color:var(--up)]')
    expect(pnl).toHaveTextContent('+₹3,428.00')
    const percent = [...reliance.querySelectorAll('span[data-stale]')].find((el) =>
      el.textContent?.endsWith('%')
    )
    expect(percent).toHaveClass(STALE)
    expect(reliance.querySelector('[data-tick]')).toHaveClass('text-muted-foreground')
    const worth = [...reliance.querySelectorAll('span')].find((el) =>
      el.textContent?.startsWith('₹58,400')
    )
    expect(worth).toHaveClass(STALE)
  })

  it('says plainly when there are no holdings, and waits for no prices', async () => {
    await renderPhone()
    expect(screen.getByText('No holdings')).toBeInTheDocument()
    await pass(60_000)
    expect(document.querySelector('[data-feed]')).toBeNull()
    expect(screen.queryByText('Waiting for prices')).toBeNull()
  })

  it('opens the page’s own ticket: Sell as its table’s Exit does, Buy more from one share', async () => {
    const tap = user()
    api.getHoldings.mockResolvedValue({
      status: 'success',
      data: { holdings: [RELIANCE], statistics: STATS },
    })
    fakeScreen('wide')
    const wide = renderBook('/holdings', <Holdings />)
    await settle()
    await tap.click(screen.getByRole('button', { name: 'Add' }))
    const fromAdd = ticket.opened.at(-1)
    await tap.click(screen.getByRole('button', { name: 'Close ticket' }))
    await tap.click(screen.getByRole('button', { name: 'Exit' }))
    const fromExit = ticket.opened.at(-1)
    expect(fromAdd).toBe('BUY RELIANCE NSE qty 40 product CNC MARKET Holdings')
    expect(fromExit).toBe('SELL RELIANCE NSE qty 40 product CNC MARKET Holdings')
    wide.unmount()
    vi.restoreAllMocks()

    await renderPhone(RELIANCE)
    await tap.click(card(/RELIANCE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Buy more' }))
    // One share, not the whole holding: a market buy of all 40 again is never one tap away.
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(
      'BUY RELIANCE NSE qty 1 product CNC MARKET Holdings'
    )
    expect(where()).toBe('/holdings?act=buy&row=NSE%3ARELIANCE')

    await tap.click(screen.getByRole('button', { name: 'Close ticket' }))
    await waitFor(() => expect(where()).toBe('/holdings'))
    await tap.click(card(/RELIANCE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Sell' }))
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(fromExit ?? '')
  })

  it('keeps the ticket, with what was typed into it, while the phone turns and back', async () => {
    const tap = user()
    const screenNow = fakeScreen('phone')
    api.getHoldings.mockResolvedValue({
      status: 'success',
      data: { holdings: [RELIANCE], statistics: STATS },
    })
    renderBook('/holdings', <Holdings />)
    await settle()
    await settle()
    await tap.click(card(/RELIANCE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Sell' }))
    await tap.click(screen.getByRole('button', { name: 'Type 1' }))
    const typed = () => screen.getByRole('region', { name: 'Order ticket' }).querySelector('output')
    expect(typed()).toHaveTextContent('typed 1')

    act(() => screenNow.turn('wide'))
    await settle()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(typed()).toHaveTextContent('typed 1')

    act(() => screenNow.turn('phone'))
    await settle()
    expect(card(/RELIANCE/)).toBeInTheDocument()
    expect(typed()).toHaveTextContent('typed 1')
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(
      'SELL RELIANCE NSE qty 40'
    )

    // Back closes the ticket the trader sees, and the next Back leaves the page.
    expect(where()).toBe('/holdings?act=sell&row=NSE%3ARELIANCE')
    back()
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull())
    expect(where()).toBe('/holdings')
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('says Buy more and Sell go to the sandbox when Analyze mode is on', async () => {
    const tap = user()
    theme.appMode = 'analyzer'
    await renderPhone(RELIANCE)
    await tap.click(card(/RELIANCE/))
    const sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('button', { name: 'Buy more in the sandbox' })).toBeEnabled()
    expect(within(sheet).getByRole('button', { name: 'Sell in the sandbox' })).toBeEnabled()
  })

  it('closes the ticket on Back, then leaves the page', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await tap.click(card(/RELIANCE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Buy more' }))
    expect(screen.getByRole('region', { name: 'Order ticket' })).toBeInTheDocument()

    back()
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull())
    expect(where()).toBe('/holdings')
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('shows the holding’s figures and its chart in the sheet', async () => {
    const tap = user()
    await renderPhone(RELIANCE)
    await tap.click(card(/RELIANCE/))
    const sheet = screen.getByRole('dialog', { name: 'RELIANCE' })
    const details = within(sheet).getByRole('region', { name: 'Details' })
    expect(details).toHaveTextContent('Current value₹58,092.00')
    expect(details).toHaveTextContent('Invested₹54,972.00')
    expect(details).toHaveTextContent("Day's change−₹348.00−0.60%")
    expect(details).toHaveTextContent('Previous close1,461.00')
    expect(details).toHaveTextContent('ProductCNC · Delivery')
    expect(
      within(sheet).getByRole('link', { name: 'Open the chart for RELIANCE on NSE' })
    ).toHaveAttribute('href', '/chart/NSE/RELIANCE')
  })
})

describe('Holdings on a wide screen', () => {
  it('keeps upstream’s table', async () => {
    api.getHoldings.mockResolvedValue({
      status: 'success',
      data: { holdings: [RELIANCE], statistics: STATS },
    })
    fakeScreen('wide')
    renderBook('/holdings', <Holdings />)
    await settle()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Trading Symbol' })).toBeInTheDocument()
    expect(screen.queryByTestId('holdings-value')).toBeNull()
  })
})
