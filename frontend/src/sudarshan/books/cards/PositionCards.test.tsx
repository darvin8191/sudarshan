/**
 * Positions on a phone (M6.9), through the real Positions page at 390px: one
 * card per position with its figures in words and signs (never colour alone),
 * prices that move with ticks and grey out when ticks stop, closed positions
 * grouped below, and a sheet whose Exit, Exit all and Add are the page's own
 * calls. Back closes what is open before it leaves the page. On a wide screen
 * the table is upstream's, unchanged.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Position } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getPositions: vi.fn(),
  getMultiQuotes: vi.fn(),
  closePosition: vi.fn(),
  closeAllPositions: vi.fn(),
  contractOf: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => api.getPositions(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    closePosition: (...args: unknown[]) => api.closePosition(...args),
    closeAllPositions: (...args: unknown[]) => api.closeAllPositions(...args),
  },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  contractOf: (...args: unknown[]) => api.contractOf(...args),
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
const theme = vi.hoisted(() => ({ appMode: 'live' }))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick(theme),
}))
vi.mock('@/utils/toast', () => ({
  showToast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

/** How often the page itself renders: it asks for the supported exchanges once a render. */
const page = vi.hoisted(() => ({ renders: 0 }))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => {
    page.renders += 1
    return { isCrypto: false }
  },
}))

const calendar = vi.hoisted(() => ({
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}))
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

// The real ticket streams its own quote and posts orders; what matters here is
// that Add opens it on the position's instrument, side, lot and product.
vi.mock('@/components/trading/PlaceOrderDialog', async () => {
  const { createElement } = await import('react')
  return {
    PlaceOrderDialog: (props: Record<string, unknown>) =>
      props.open
        ? createElement(
            'section',
            { 'aria-label': 'Order ticket' },
            [
              props.action,
              props.symbol,
              props.exchange,
              `lot ${props.lotSize}`,
              `tick ${props.tickSize}`,
              `product ${props.product ?? 'default'}`,
              props.strategy,
            ].join(' '),
            createElement(
              'button',
              {
                type: 'button',
                onClick: () => (props.onOpenChange as (open: boolean) => void)(false),
              },
              'Close ticket'
            )
          )
        : null,
  }
})

import Positions from '@/pages/Positions'
import { back, frame, pass, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { fakeScreen } from '@/test/screenWidth'
import { STALE } from './parts'

vi.setConfig({ testTimeout: 15_000 })

const CALL = 'NIFTY27OCT2625900CE'
const PUT = 'BANKNIFTY27OCT2657500PE'
const FUT = 'NIFTY27OCT26FUT'

/** Bought 65 at 142.50, the book read at 152.50: +650 open. */
const LONG = {
  symbol: CALL,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 142.5,
  ltp: 152.5,
  pnl: 650,
  pnlpercent: 7.02,
  realised: 0,
  unrealised: 650,
  day_buy_quantity: 65,
  day_buy_value: 9262.5,
  day_sell_quantity: 0,
  day_sell_value: 0,
  overnight_quantity: 0,
} as Position

/** Sold 30 at 228.40, carried: +483 open. */
const SHORT = {
  symbol: PUT,
  exchange: 'NFO',
  product: 'NRML',
  quantity: -30,
  average_price: 228.4,
  ltp: 212.3,
  pnl: 483,
  pnlpercent: 7.05,
  realised: 0,
  unrealised: 483,
} as Position

/** Squared off today with 1,200 booked. */
const CLOSED = {
  symbol: FUT,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 0,
  average_price: 0,
  ltp: 25921.4,
  pnl: 1200,
  pnlpercent: 0,
  realised: 1200,
  unrealised: 0,
} as Position

/**
 * Carried in from yesterday, as Upstox's book reads it (sudarshan/books/tests, CARRIED):
 * bought 65 at 100, closed yesterday at 118.40, last 130. Realised and unrealised are the
 * day's (0 and 754), pnl the net (1,950): they do not add up, so only the total is shown.
 */
const CARRIED = {
  symbol: 'NIFTY27OCT2625700CE',
  exchange: 'NFO',
  product: 'NRML',
  quantity: 65,
  average_price: 100,
  ltp: 130,
  pnl: 1950,
  pnlpercent: 30,
  realised: 0,
  unrealised: 754,
  day_buy_quantity: 0,
  day_buy_value: 0,
  day_sell_quantity: 0,
  day_sell_value: 0,
  overnight_quantity: 65,
} as Position

/** Carried in and squared off today: 900 net, of which the day's part is 300. */
const CLOSED_CARRIED = {
  symbol: 'BANKNIFTY27OCT2657000CE',
  exchange: 'NFO',
  product: 'NRML',
  quantity: 0,
  average_price: 0,
  ltp: 410,
  pnl: 900,
  pnlpercent: 0,
  realised: 300,
  unrealised: 0,
} as Position

function book(...positions: Position[]) {
  return { status: 'success', data: positions }
}

function tick(symbol: string, data: Record<string, unknown>) {
  act(() => {
    feed.push(symbol, 'NFO', data)
    frames.flush()
  })
}

function card(name: RegExp) {
  return screen.getByRole('button', { name })
}

async function renderPhone(...positions: Position[]) {
  api.getPositions.mockResolvedValue(book(...positions))
  fakeScreen('phone')
  renderBook('/positions', <Positions />)
  await settle()
}

function user() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
}

beforeEach(() => {
  fakeClock()
  theme.appMode = 'live'
  frames.install()
  feed.reset()
  socket.clear()
  localStorage.clear()
  page.renders = 0
  api.getPositions.mockReset()
  api.getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  api.closePosition.mockReset().mockResolvedValue({ status: 'success', message: 'Closed' })
  api.closeAllPositions.mockReset().mockResolvedValue({ status: 'success' })
  api.contractOf.mockReset().mockResolvedValue({ lotSize: 65, tickSize: 0.05 })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Positions on a phone: the cards', () => {
  it('shows each open position as a card, its figures carried by signs and words', async () => {
    await renderPhone(LONG, SHORT)
    expect(screen.queryByRole('table')).toBeNull()

    const long = card(/NIFTY 25900 CE/)
    expect(long).toHaveTextContent('MIS')
    expect(long).toHaveTextContent('+65LONG')
    expect(long).toHaveTextContent('NFO · 27 Oct 2026 · Avg 142.50')
    expect(long).toHaveTextContent('LTP152.50')
    expect(long).toHaveTextContent('+₹650.00')
    expect(long).toHaveTextContent('+7.02%')
    // The P&L's arrow points the way its sign does.
    expect(long.querySelector('span[data-direction="up"] > svg')).toHaveAttribute(
      'data-direction',
      'up'
    )

    const short = card(/BANKNIFTY 57500 PE/)
    expect(short).toHaveTextContent('NRML')
    expect(short).toHaveTextContent('−30SHORT')
    const side = short.querySelector('[data-side="SHORT"]')
    expect(side).toHaveClass('text-[color:var(--sell-solid)]')
    expect(long.querySelector('[data-side="LONG"]')).toHaveClass('text-[color:var(--buy-solid)]')
  })

  it('totals the P&L in one big figure, split into realised and unrealised', async () => {
    await renderPhone(LONG, SHORT, CLOSED)
    const total = screen.getByTestId('positions-total')
    expect(total).toHaveTextContent('+₹2,333.00')
    expect(total.querySelector('svg[data-direction="up"]')).not.toBeNull()
    expect(total.querySelector('.sd-display')).not.toBeNull()
    const summary = screen.getByRole('region', { name: 'Total P&L' })
    expect(summary).toHaveTextContent('Realised +₹1,200.00')
    expect(summary).toHaveTextContent('Unrealised +₹1,133.00')
    expect(summary).toHaveTextContent('2 open positions')
  })

  it('shows the total alone when a position’s booked and open do not add up to its P&L', async () => {
    await renderPhone(LONG, CARRIED)
    const summary = screen.getByRole('region', { name: 'Total P&L' })
    expect(screen.getByTestId('positions-total')).toHaveTextContent('+₹2,600.00')
    expect(summary).not.toHaveTextContent('Realised')
    expect(summary).not.toHaveTextContent('Unrealised')
  })

  it('keeps a closed position’s booked figure at its P&L, and the total alone', async () => {
    await renderPhone(LONG, CLOSED_CARRIED)
    const closed = screen.getByRole('region', { name: 'Closed (1)' })
    const row = within(closed).getByRole('button', { name: /BANKNIFTY 57000 CE/ })
    expect(row).toHaveTextContent('+₹900.00')
    expect(row).toHaveTextContent('Booked')
    expect(screen.getByRole('region', { name: 'Total P&L' })).not.toHaveTextContent('Realised')
  })

  it('moves the P&L and the LTP with every tick, with the last tick’s arrow', async () => {
    await renderPhone(LONG, SHORT)

    tick(CALL, { ltp: 154.5 })
    let long = card(/NIFTY 25900 CE/)
    // 650 + (154.50 - 152.50) x 65
    expect(long).toHaveTextContent('+₹780.00')
    expect(long.querySelector('[data-tick="up"]')).toHaveTextContent('154.50')

    tick(CALL, { ltp: 150 })
    long = card(/NIFTY 25900 CE/)
    expect(long).toHaveTextContent('+₹487.50')
    expect(long.querySelector('[data-tick="down"]')).toHaveTextContent('150.00')
    expect(screen.getByTestId('positions-total')).toHaveTextContent('+₹970.50')
    expect(screen.getByText('Real-time')).toBeInTheDocument()
  })

  it('greys a position whose ticks have stopped, keeping its sign, arrow and a clock', async () => {
    await renderPhone(LONG)
    tick(CALL, { ltp: 149 })
    const total = screen.getByTestId('positions-total')
    expect(total).toHaveClass('text-[color:var(--up)]')
    await pass(12_000)

    const long = card(/NIFTY 25900 CE/)
    expect(long.closest('li')).toHaveAttribute('data-stale', 'true')
    const pnl = long.querySelector('span[data-direction]') as HTMLElement
    expect(pnl).toHaveAttribute('data-stale', 'true')
    expect(pnl).toHaveClass(STALE)
    expect(pnl).not.toHaveClass('text-[color:var(--up)]')
    expect(pnl).toHaveTextContent('+₹422.50')
    expect(pnl.querySelector(':scope > svg')).toHaveAttribute('data-direction', 'up')
    const percent = [...long.querySelectorAll('span[data-stale]')].find((el) =>
      el.textContent?.endsWith('%')
    )
    expect(percent).toHaveClass(STALE)
    const ltp = long.querySelector('[data-tick="down"]') as HTMLElement
    expect(ltp).toHaveAttribute('data-stale', 'true')
    expect(ltp).toHaveClass('text-muted-foreground')
    // The big total too: grey, still signed and with its arrow.
    expect(screen.getByTestId('positions-total')).toHaveClass(STALE)
    expect(screen.getByTestId('positions-total')).not.toHaveClass('text-[color:var(--up)]')
    expect(screen.getByTestId('positions-total')).toHaveTextContent('+₹422.50')
    expect(screen.getByText(/Stale · updated 12s ago/)).toBeInTheDocument()
  })

  it('points a losing position’s arrow down, its own arrow, not the last tick’s', async () => {
    // Bought at 142.50, now 130: a loss, while the last tick was up.
    await renderPhone({ ...LONG, ltp: 129, pnl: -877.5, pnlpercent: -9.47 } as Position)
    tick(CALL, { ltp: 130 })
    const long = card(/NIFTY 25900 CE/)
    const pnl = long.querySelector('span[data-direction]') as HTMLElement
    expect(pnl).toHaveAttribute('data-direction', 'down')
    expect(pnl.querySelector(':scope > svg')).toHaveAttribute('data-direction', 'down')
    expect(long.querySelector('[data-tick="up"]')).toHaveTextContent('130.00')
    const total = screen.getByTestId('positions-total')
    expect(total.querySelector(':scope > svg')).toHaveAttribute('data-direction', 'down')
  })

  it('groups closed positions below, muted, with what they booked', async () => {
    await renderPhone(LONG, CLOSED)
    const open = screen.getByRole('list', { name: 'Open positions' })
    expect(within(open).queryByText('NIFTY FUT')).toBeNull()

    const closed = screen.getByRole('region', { name: 'Closed (1)' })
    const row = within(closed).getByRole('button', { name: /NIFTY FUT/ })
    expect(row).toHaveTextContent('+₹1,200.00')
    expect(row).toHaveTextContent('Booked')
    expect(row).toHaveTextContent('Closed')
    expect(row).toHaveClass('bg-[color:var(--surface-2)]')
  })

  it('subscribes only what is held: a closed position’s figures are the book’s', async () => {
    await renderPhone(LONG, CLOSED)
    expect(feed.live()).toContain(`NFO:${CALL}:LTP`)
    expect(feed.opened.filter((key) => key.includes(FUT))).toEqual([])
    const closed = screen.getByRole('region', { name: 'Closed (1)' })
    expect(within(closed).getByRole('button', { name: /NIFTY FUT/ })).toHaveTextContent(
      '+₹1,200.00'
    )
    expect(screen.getByTestId('positions-total')).toHaveTextContent('+₹1,850.00')
  })

  it('draws Exit all in the danger colour, which colour-blind mode keeps red', async () => {
    await renderPhone(LONG)
    const all = screen.getByRole('button', { name: 'Exit all' })
    expect(all).toHaveClass('border-destructive', 'text-destructive')
    expect(all.getAttribute('class')).not.toContain('--down')
  })

  it('says plainly when there are no positions', async () => {
    await renderPhone()
    expect(screen.getByText('No open positions')).toBeInTheDocument()
  })

  it('shows every position, whatever filters the wide table saved on this device', async () => {
    localStorage.setItem(
      'openalgo_positions_prefs',
      JSON.stringify({
        grouping: 'none',
        filters: { product: ['CNC'], direction: [], exchange: [] },
      })
    )
    await renderPhone(LONG, SHORT)
    expect(card(/NIFTY 25900 CE/)).toBeInTheDocument()
    expect(card(/BANKNIFTY 57500 PE/)).toBeInTheDocument()
  })
})

describe('Positions on a phone: the sheet and its actions', () => {
  it('exits through the page’s own close call, once, after a confirmation', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT)
    await tap.click(card(/NIFTY 25900 CE/))
    const sheet = screen.getByRole('dialog', { name: 'NIFTY 25900 CE' })
    expect(sheet).toHaveTextContent('Sell 65 at market')

    await tap.click(within(sheet).getByRole('button', { name: /^Exit/ }))
    const confirm = screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })
    expect(confirm).toHaveTextContent('Sells 65 (MIS) at market price')
    expect(api.closePosition).not.toHaveBeenCalled()

    await tap.dblClick(within(confirm).getByRole('button', { name: 'Exit' }))
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    expect(api.closePosition).toHaveBeenCalledWith(CALL, 'NFO', 'MIS')
    await waitFor(() => expect(where()).toBe('/positions'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('marks the position "Exit sent" and holds its Exit until the book shows the change', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).getByRole('button', {
        name: 'Exit',
      })
    )
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    // The broker's book lags the order: the re-read after it still says 65.
    expect(card(/NIFTY 25900 CE/)).toHaveTextContent('Exit sent')
    expect(card(/BANKNIFTY 57500 PE/)).not.toHaveTextContent('Exit sent')
    expect(screen.getByRole('button', { name: 'Exit all' })).toBeDisabled()
    await tap.click(card(/NIFTY 25900 CE/))
    const exit = within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ })
    expect(exit).toBeDisabled()
    expect(exit).toHaveTextContent('Exit sent')
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))

    // The book catches up: the position is closed, and nothing is held any more.
    api.getPositions.mockResolvedValue(book(SHORT, { ...LONG, quantity: 0, pnl: 650 } as Position))
    await tap.click(screen.getByRole('button', { name: 'Refresh' }))
    await settle()
    expect(screen.queryByText('Exit sent')).toBeNull()
    expect(screen.getByRole('button', { name: 'Exit all' })).toBeEnabled()
  })

  it('offers Exit again 15 s after an exit the book never showed', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Exit' }))
    await settle()
    expect(card(/NIFTY 25900 CE/)).toHaveTextContent('Exit sent')

    await pass(14_000)
    expect(card(/NIFTY 25900 CE/)).toHaveTextContent('Exit sent')
    await pass(1_500)
    expect(card(/NIFTY 25900 CE/)).not.toHaveTextContent('Exit sent')
    await tap.click(card(/NIFTY 25900 CE/))
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ })).toBeEnabled()
  })

  it('holds nothing when the exit was refused, so it can be tried again', async () => {
    const tap = user()
    api.closePosition.mockResolvedValue({ status: 'error', message: 'Not now' })
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Exit' }))
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    expect(card(/NIFTY 25900 CE/)).not.toHaveTextContent('Exit sent')
    expect(screen.getByRole('button', { name: 'Exit all' })).toBeEnabled()
  })

  it('marks every open position "Exit sent" after Exit all', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT, CLOSED)
    await tap.click(screen.getByRole('button', { name: 'Exit all' }))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Exit all' }))
    await settle()
    expect(api.closeAllPositions).toHaveBeenCalledTimes(1)
    expect(card(/NIFTY 25900 CE/)).toHaveTextContent('Exit sent')
    expect(card(/BANKNIFTY 57500 PE/)).toHaveTextContent('Exit sent')
    expect(screen.getByRole('button', { name: 'Exit all' })).toBeDisabled()
    await pass(15_500)
    expect(screen.queryByText('Exit sent')).toBeNull()
  })

  it('says an exit goes to the sandbox when Analyze mode is on', async () => {
    const tap = user()
    theme.appMode = 'analyzer'
    await renderPhone(LONG, SHORT)
    await tap.click(card(/NIFTY 25900 CE/))
    const sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('button', { name: /^Exit/ })).toHaveTextContent(
      'Sell 65 in the sandbox'
    )
    const add = within(sheet).getByRole('button', { name: /^Add/ })
    expect(add).toHaveTextContent('Buy more in the sandbox')
    await tap.click(within(sheet).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toHaveTextContent(
      'Sells 65 (MIS) at market price in the sandbox to close this position. ' +
        'Analyze mode is on, so nothing goes to Upstox.'
    )
    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    await tap.click(screen.getByRole('button', { name: 'Exit all' }))
    expect(screen.getByRole('dialog', { name: 'Exit all positions?' })).toHaveTextContent(
      'This closes all 2 open positions at market price in the sandbox. ' +
        'Analyze mode is on, so nothing goes to Upstox.'
    )
  })

  it('names no sandbox when orders go to the broker', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    const sheet = screen.getByRole('dialog')
    expect(sheet).not.toHaveTextContent('sandbox')
    await tap.click(within(sheet).getByRole('button', { name: /^Exit/ }))
    const confirm = screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })
    expect(confirm).toHaveTextContent('This cannot be undone.')
    expect(confirm).not.toHaveTextContent('sandbox')
  })

  it('exits the position whose sheet it is: the second card’s, not the first', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT)
    await tap.click(card(/BANKNIFTY 57500 PE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit BANKNIFTY 57500 PE?' })).getByRole('button', {
        name: 'Exit',
      })
    )
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    expect(api.closePosition).toHaveBeenCalledWith(PUT, 'NFO', 'NRML')
  })

  it('sends one exit for two taps in the same moment', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    const exit = within(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).getByRole(
      'button',
      { name: 'Exit' }
    )
    act(() => {
      exit.click()
      exit.click()
    })
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })

  it('closes an exit confirmation once the book shows the position closed, sending nothing', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toBeInTheDocument()

    // Squared off elsewhere (the broker's app, a stop): the next read says so.
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 0, pnl: 650 } as Position, SHORT))
    act(() =>
      socket.fire('order_update', { mode: 'live', orderid: 'x1', order_status: 'complete' })
    )
    await pass(3_000)
    await settle()
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toBeNull()
    )
    expect(api.closePosition).not.toHaveBeenCalled()
  })

  it('makes the same call as the wide table’s close button', async () => {
    const tap = user()
    api.getPositions.mockResolvedValue(book(SHORT))
    fakeScreen('wide')
    const wide = renderBook('/positions', <Positions />)
    await settle()
    await tap.click(screen.getByRole('button', { name: `Close ${PUT} position` }))
    expect(api.closePosition).toHaveBeenLastCalledWith(PUT, 'NFO', 'NRML')

    wide.unmount()
    vi.restoreAllMocks()
    api.closePosition.mockClear()
    await renderPhone(SHORT)
    await tap.click(card(/BANKNIFTY 57500 PE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    const confirm = screen.getByRole('dialog', { name: 'Exit BANKNIFTY 57500 PE?' })
    expect(confirm).toHaveTextContent('Buys 30 (NRML) at market price')
    await tap.click(within(confirm).getByRole('button', { name: 'Exit' }))
    expect(api.closePosition).toHaveBeenLastCalledWith(PUT, 'NFO', 'NRML')
  })

  it('exits everything through the page’s close-all, after a confirmation', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT, CLOSED)
    await tap.click(screen.getByRole('button', { name: 'Exit all' }))
    const confirm = screen.getByRole('dialog', { name: 'Exit all positions?' })
    expect(confirm).toHaveTextContent('closes all 2 open positions at market price')
    await tap.click(within(confirm).getByRole('button', { name: 'Exit all' }))
    expect(api.closeAllPositions).toHaveBeenCalledTimes(1)
  })

  it('offers no Exit all and no Exit when every position is closed', async () => {
    const tap = user()
    await renderPhone(CLOSED)
    expect(screen.getByRole('button', { name: 'Exit all' })).toBeDisabled()
    await tap.click(card(/NIFTY FUT/))
    const sheet = screen.getByRole('dialog', { name: 'NIFTY FUT' })
    expect(within(sheet).queryByRole('button', { name: /^Exit/ })).toBeNull()
    expect(within(sheet).queryByRole('button', { name: /^Add/ })).toBeNull()
    expect(sheet).toHaveTextContent('Realised+₹1,200.00')
    expect(api.closePosition).not.toHaveBeenCalled()
  })

  it('adds through the app’s order ticket on the same side and product, one lot', async () => {
    const tap = user()
    await renderPhone(LONG, SHORT)
    await tap.click(card(/BANKNIFTY 57500 PE/))
    const add = within(screen.getByRole('dialog')).getByRole('button', { name: /^Add/ })
    await waitFor(() => expect(add).toBeEnabled())
    await tap.click(add)
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(
      `SELL ${PUT} NFO lot 65 tick 0.05 product NRML Positions`
    )
    expect(api.contractOf).toHaveBeenCalledWith('test-api-key', PUT, 'NFO')
  })

  it('shows the broker’s details: booked, open, bought and sold today, carried', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    const details = within(screen.getByRole('dialog')).getByRole('region', { name: 'Details' })
    expect(details).toHaveTextContent('Average price142.50')
    expect(details).toHaveTextContent('Realised₹0.00')
    expect(details).toHaveTextContent('Unrealised+₹650.00')
    expect(details).toHaveTextContent('Bought today65 · ₹9,262.50')
    expect(details).toHaveTextContent('Sold today0 · ₹0.00')
    expect(details).toHaveTextContent('Overnight quantity0')
    expect(details).toHaveTextContent('ProductMIS · Intraday')

    tick(CALL, { ltp: 154.5 })
    expect(details).toHaveTextContent('Unrealised+₹780.00')
  })

  it('leaves booked and open out of a carried position’s sheet: they do not add up', async () => {
    const tap = user()
    await renderPhone(CARRIED)
    await tap.click(card(/NIFTY 25700 CE/))
    const sheet = screen.getByRole('dialog', { name: 'NIFTY 25700 CE' })
    expect(sheet).toHaveTextContent('+₹1,950.00')
    const details = within(sheet).getByRole('region', { name: 'Details' })
    expect(details).not.toHaveTextContent('Realised')
    expect(details).not.toHaveTextContent('Unrealised')
    expect(details).toHaveTextContent('Overnight quantity+65')
  })

  it('leaves out the details a broker does not send', async () => {
    const tap = user()
    const bare = { ...LONG } as Record<string, unknown>
    for (const key of ['realised', 'unrealised', 'day_buy_quantity', 'day_buy_value'])
      delete bare[key]
    for (const key of ['day_sell_quantity', 'day_sell_value', 'overnight_quantity'])
      delete bare[key]
    await renderPhone(bare as unknown as Position)
    expect(screen.getByRole('region', { name: 'Total P&L' })).not.toHaveTextContent('Realised')
    await tap.click(card(/NIFTY 25900 CE/))
    const details = within(screen.getByRole('dialog')).getByRole('region', { name: 'Details' })
    expect(details).not.toHaveTextContent('Bought today')
    expect(details).not.toHaveTextContent('Overnight')
  })

  it('opens the phone chart from the sheet', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    const chart = within(screen.getByRole('dialog')).getByRole('link', {
      name: `Open the chart for ${CALL} on NFO`,
    })
    expect(chart).toHaveAttribute('href', `/chart/NFO/${CALL}`)
  })
})

describe('Positions on a phone and the Back button', () => {
  it('closes the sheet on Back, then leaves the page', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    expect(where()).toBe(`/positions?row=${encodeURIComponent(`NFO:${CALL}:MIS`)}`)

    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(where()).toBe('/positions')
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('leaves the same history when the sheet is closed by its button', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(where()).toBe('/positions'))
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('cancels an exit on Back, back to the cards, with nothing sent', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toBeInTheDocument()

    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(where()).toBe('/positions')
    expect(api.closePosition).not.toHaveBeenCalled()
  })

  it('closes the order ticket on Back', async () => {
    const tap = user()
    await renderPhone(LONG)
    await tap.click(card(/NIFTY 25900 CE/))
    const add = within(screen.getByRole('dialog')).getByRole('button', { name: /^Add/ })
    await waitFor(() => expect(add).toBeEnabled())
    await tap.click(add)
    expect(screen.getByRole('region', { name: 'Order ticket' })).toBeInTheDocument()

    back()
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull())
    expect(where()).toBe('/positions')
  })

  it('never reopens a confirmation from the address: an exit opens only from a tap', async () => {
    api.getPositions.mockResolvedValue(book(LONG))
    fakeScreen('phone')
    const row = encodeURIComponent(`NFO:${CALL}:MIS`)
    renderBook('/positions', <Positions />, ['/dashboard', `/positions?act=exit&row=${row}`])
    await settle()
    await waitFor(() => expect(where()).toBe('/positions'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('Positions on a wide screen', () => {
  it('keeps upstream’s table, and follows the screen as it turns', async () => {
    api.getPositions.mockResolvedValue(book(LONG, CLOSED))
    const screenNow = fakeScreen('wide')
    renderBook('/positions', <Positions />)
    await settle()

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Symbol/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Close ${CALL} position` })).toBeInTheDocument()
    expect(screen.queryByTestId('positions-total')).toBeNull()

    act(() => screenNow.turn('phone'))
    expect(screen.queryByRole('table')).toBeNull()
    expect(card(/NIFTY 25900 CE/)).toBeInTheDocument()

    act(() => screenNow.turn('wide'))
    expect(screen.getByRole('table')).toBeInTheDocument()
    frame()
  })
})
