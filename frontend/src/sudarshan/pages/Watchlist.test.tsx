/**
 * The phone watchlist, end to end in jsdom. The lists API is an in-memory
 * stand-in for blueprints/watchlist.py, the market-data manager is the shared
 * test harness (so `useMarketData` and its subscription lifecycle are real),
 * and the order ticket is a stub that shows what it was opened with. What is
 * pinned is what the owner sees and what the page asks of the server.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

interface Item {
  id: number
  symbol: string
  exchange: string
  position: number
}
interface List {
  id: number
  name: string
  position: number
  items: Item[]
}

const server = vi.hoisted(() => {
  const state = { lists: [] as List[], nextId: 1000 }
  const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
  const refuse = (message: string) =>
    Object.assign(new Error(message), { response: { data: { message } } })
  const find = (id: number) => {
    const list = state.lists.find((each) => each.id === id)
    if (!list) throw refuse('List not found')
    return list
  }
  const api = {
    list: vi.fn(async () => copy(state.lists)),
    create: vi.fn(async (name: string) => {
      if (state.lists.some((list) => list.name === name)) {
        throw refuse(`A list named "${name}" already exists`)
      }
      const list = { id: state.nextId++, name, position: state.lists.length, items: [] }
      state.lists.push(list)
      return copy(list)
    }),
    rename: vi.fn(async (id: number, name: string) => {
      find(id).name = name
    }),
    remove: vi.fn(async (id: number) => {
      state.lists = state.lists.filter((list) => list.id !== id)
    }),
    clear: vi.fn(),
    addItem: vi.fn(async (id: number, symbol: string, exchange: string) => {
      const list = find(id)
      const item = { id: state.nextId++, symbol, exchange, position: list.items.length }
      list.items.push(item)
      return copy(item)
    }),
    removeItem: vi.fn(async (id: number, itemId: number) => {
      const list = find(id)
      list.items = list.items.filter((item) => item.id !== itemId)
    }),
    reorderItems: vi.fn(async (id: number, order: number[]) => {
      const list = find(id)
      list.items = order.map((itemId, position) => ({
        ...(list.items.find((item) => item.id === itemId) as Item),
        position,
      }))
    }),
  }
  return { state, api }
})

vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: server.api,
}))

const calendar = vi.hoisted(() => {
  const state = { open: true }
  return {
    state,
    value: {
      isLoading: false,
      error: null,
      isMarketOpen: () => state.open,
      isAnyMarketOpen: () => state.open,
    },
  }
})
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar.value }))

const remote = vi.hoisted(() => ({
  getMultiQuotes: vi.fn(),
  searchSymbols: vi.fn(),
  contractOf: vi.fn(),
  getUnderlyings: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => remote.getMultiQuotes(...args) },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  searchSymbols: (...args: unknown[]) => remote.searchSymbols(...args),
  contractOf: (...args: unknown[]) => remote.contractOf(...args),
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: (...args: unknown[]) => remote.getUnderlyings(...args) },
}))

// The real ticket streams its own quote and posts orders; what matters here is
// that the sheet opens it, for the right instrument, side and contract.
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
              props.strategy,
            ].join(' ')
          )
        : null,
  }
})

import { useAuthStore } from '@/stores/authStore'
import { STALE_MS } from '@/sudarshan/dashboard/live'
import { feed } from '@/test/marketDataHarness'
import Watchlist, { OPEN_LIST_KEY } from './Watchlist'

const MINUS = '−'

const MAIN: List = {
  id: 1,
  name: 'Main',
  position: 0,
  items: [
    { id: 11, symbol: 'SBIN', exchange: 'NSE', position: 0 },
    { id: 12, symbol: 'NIFTY', exchange: 'NSE_INDEX', position: 1 },
  ],
}
const OPTIONS: List = {
  id: 2,
  name: 'Options',
  position: 1,
  items: [{ id: 21, symbol: 'NIFTY28OCT2625000CE', exchange: 'NFO', position: 0 }],
}

/** Last price and previous close, as the snapshot reads them. */
const QUOTES: Record<string, [number, number]> = {
  'NSE:SBIN': [812.4, 800],
  'NSE_INDEX:NIFTY': [25874.3, 25714.85],
  'NFO:NIFTY28OCT2625000CE': [125.4, 112.95],
  'NSE:HDFCBANK': [1640, 1650],
}

function answer(_apiKey: string, symbols: Array<{ symbol: string; exchange: string }>) {
  return Promise.resolve({
    status: 'success',
    results: symbols.map(({ symbol, exchange }) => {
      const [ltp, prev_close] = QUOTES[`${exchange}:${symbol}`] ?? [0, 0]
      return {
        symbol,
        exchange,
        data: { ltp, prev_close, open: 0, high: 0, low: 0, volume: 0, oi: 0, bid: 0, ask: 0 },
      }
    }),
  })
}

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

/** Let promises settle inside React's act. */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
}

/** Move the fake clock on, running every timer due on the way. */
async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function tick(symbol: string, exchange: string, data: Record<string, unknown>) {
  act(() => feed.push(symbol, exchange, data))
}

const row = (name: RegExp) => screen.getByRole('button', { name })
const statusLine = (container: HTMLElement) => container.querySelector('[data-feed]')

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, item: string) {
  await user.click(screen.getByRole('button', { name: 'List options' }))
  await user.click(await screen.findByRole('menuitem', { name: item }))
}

beforeEach(() => {
  feed.reset()
  localStorage.clear()
  server.state.lists = JSON.parse(JSON.stringify([MAIN, OPTIONS]))
  server.state.nextId = 1000
  for (const fn of Object.values(server.api)) fn.mockClear()
  calendar.state.open = true
  remote.getMultiQuotes.mockReset()
  remote.getMultiQuotes.mockImplementation(answer)
  remote.searchSymbols.mockReset()
  remote.contractOf.mockReset()
  remote.contractOf.mockResolvedValue({ lotSize: 750, tickSize: 0.05 })
  remote.getUnderlyings.mockReset()
  remote.getUnderlyings.mockResolvedValue({ status: 'success', underlyings: ['NIFTY', 'SBIN'] })
  useAuthStore.setState({ apiKey: 'key' })
  setVisibility('visible')
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Watchlist lists', () => {
  it('shows each list as a tab and opens the one last used on this device', async () => {
    localStorage.setItem(OPEN_LIST_KEY, '2')
    renderPage()

    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Main2', 'Options1'])
    expect(screen.getByRole('tab', { name: /Options/ })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('button', { name: /^NIFTY 25000 CE/ })).toHaveTextContent(
      'NFO · 28 Oct 2026'
    )
  })

  it('moves between lists with the arrow keys, as tabs do', async () => {
    const user = userEvent.setup()
    renderPage()
    const main = await screen.findByRole('tab', { name: /Main/ })
    expect(main).toHaveAttribute('tabindex', '0')

    main.focus()
    await user.keyboard('{ArrowRight}')
    const options = screen.getByRole('tab', { name: /Options/ })
    expect(options).toHaveAttribute('aria-selected', 'true')
    expect(options).toHaveFocus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: /Main/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('makes the first list when there is none, as the terminal does', async () => {
    server.state.lists = []
    renderPage()

    expect(await screen.findByRole('tab', { name: /Watchlist/ })).toBeInTheDocument()
    expect(server.api.create).toHaveBeenCalledWith('Watchlist')
    expect(screen.getByText('This list is empty')).toBeInTheDocument()
  })

  it('makes a list and opens it', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await openMenu(user, 'New list')
    await user.type(screen.getByRole('textbox', { name: 'List name' }), 'Banks')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(server.api.create).toHaveBeenCalledWith('Banks')
    expect(await screen.findByRole('tab', { name: /Banks/ })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(localStorage.getItem(OPEN_LIST_KEY)).toBe('1000')
  })

  it('keeps the dialog open with the server reason when a name is in use', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await openMenu(user, 'New list')
    await user.type(screen.getByRole('textbox', { name: 'List name' }), 'Options')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A list named "Options" already exists'
    )
    expect(screen.getByRole('dialog', { name: 'New list' })).toBeInTheDocument()
  })

  it('renames the open list', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await openMenu(user, 'Rename list')
    const name = screen.getByRole('textbox', { name: 'List name' })
    expect(name).toHaveValue('Main')
    await user.clear(name)
    await user.type(name, 'Core')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(server.api.rename).toHaveBeenCalledWith(1, 'Core')
    expect(await screen.findByRole('tab', { name: /Core/ })).toHaveAttribute(
      'aria-selected',
      'true'
    )
  })

  it('deletes the open list after saying what goes with it', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await openMenu(user, 'Delete list')
    const dialog = screen.getByRole('dialog', { name: 'Delete “Main”?' })
    expect(dialog).toHaveTextContent('Its 2 symbols go with it.')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
    expect(server.api.remove).toHaveBeenCalledWith(1)
    await waitFor(() => expect(screen.queryByRole('tab', { name: /Main/ })).toBeNull())
    expect(screen.getByRole('tab', { name: /Options/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('adds symbols from the search, marks one already in the list, and removes one', async () => {
    const user = userEvent.setup()
    remote.searchSymbols.mockResolvedValue([
      { symbol: 'HDFCBANK', exchange: 'NSE', name: 'HDFC BANK LTD' },
      { symbol: 'SBIN', exchange: 'NSE', name: 'STATE BANK OF INDIA' },
    ])
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await user.click(screen.getByRole('button', { name: 'Add' }))
    const sheet = screen.getByRole('dialog', { name: 'Add to Main' })
    await user.type(within(sheet).getByRole('searchbox', { name: 'Search symbols' }), 'BANK')
    const add = await within(sheet).findByRole('button', { name: 'Add HDFCBANK, NSE' })
    expect(remote.searchSymbols).toHaveBeenCalledWith('key', 'BANK')
    expect(within(sheet).getByRole('button', { name: 'SBIN, NSE, in the list' })).toBeDisabled()

    await user.click(add)
    expect(server.api.addItem).toHaveBeenCalledWith(1, 'HDFCBANK', 'NSE')
    expect(
      await within(sheet).findByRole('button', { name: 'HDFCBANK, NSE, in the list' })
    ).toBeDisabled()

    await user.click(within(sheet).getByRole('button', { name: 'Close' }))
    expect(await screen.findByRole('button', { name: /^HDFCBANK/ })).toHaveTextContent('1,640.00')

    await openMenu(user, 'Reorder or remove')
    await user.click(screen.getByRole('button', { name: 'Remove HDFCBANK' }))
    expect(server.api.removeItem).toHaveBeenCalledWith(1, 1000)
    await waitFor(() => expect(screen.queryByText('HDFCBANK')).toBeNull())
  })

  it('moves a symbol a place down and saves the whole order', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await openMenu(user, 'Reorder or remove')
    expect(screen.getByRole('button', { name: 'Move SBIN up' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Move SBIN down' }))
    expect(server.api.reorderItems).toHaveBeenCalledWith(1, [12, 11])
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Move SBIN down' })).toBeDisabled()
    )

    await user.click(screen.getByRole('button', { name: 'Done' }))
    const rows = screen.getAllByRole('button', { name: /^(SBIN|NIFTY)/ })
    expect(rows[0]).toHaveTextContent(/^NIFTYNSE INDEX/)
    expect(rows[1]).toHaveTextContent(/^SBINNSE/)
  })

  it('offers to add symbols to an empty list', async () => {
    const user = userEvent.setup()
    server.state.lists[0].items = []
    renderPage()

    expect(await screen.findByText('This list is empty')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add symbols' }))
    expect(screen.getByRole('dialog', { name: 'Add to Main' })).toBeInTheDocument()
  })
})

describe('Watchlist prices', () => {
  it('starts from one snapshot, then moves each row with its ticks', async () => {
    const { container } = renderPage()
    expect(await screen.findByRole('button', { name: /^SBIN/ })).toHaveTextContent(
      '812.40+12.40 (+1.55%)'
    )
    expect(remote.getMultiQuotes).toHaveBeenCalledWith('key', [
      { symbol: 'SBIN', exchange: 'NSE' },
      { symbol: 'NIFTY', exchange: 'NSE_INDEX' },
    ])

    tick('SBIN', 'NSE', { ltp: 820 })
    expect(row(/^SBIN/)).toHaveTextContent('820.00+20.00 (+2.50%)')
    expect(row(/^SBIN/).querySelector('[data-tick]')).toHaveAttribute('data-tick', 'up')
    expect(statusLine(container)).toHaveTextContent('Real-time')

    tick('SBIN', 'NSE', { ltp: 815.5 })
    expect(row(/^SBIN/)).toHaveTextContent('815.50+15.50 (+1.94%)')
    expect(row(/^SBIN/).querySelector('[data-tick]')).toHaveAttribute('data-tick', 'down')

    // A tick at the same price keeps the last direction.
    tick('SBIN', 'NSE', { ltp: 815.5 })
    expect(row(/^SBIN/).querySelector('[data-tick]')).toHaveAttribute('data-tick', 'down')

    tick('SBIN', 'NSE', { ltp: 795 })
    expect(row(/^SBIN/)).toHaveTextContent(`795.00${MINUS}5.00 (${MINUS}0.63%)`)
    // One read for the snapshot; the ticks did the rest.
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(1)
  })

  it("measures the change from a tick's prev_close once ticks carry one", async () => {
    renderPage()
    await screen.findByRole('button', { name: /^SBIN/ })

    // The completed LTP payload (M11.3): prev_close and last_trade_time ride along.
    tick('SBIN', 'NSE', { ltp: 820, prev_close: 805, last_trade_time: Date.now() - 400 })
    expect(row(/^SBIN/)).toHaveTextContent('820.00+15.00 (+1.86%)')

    // A frame that leaves prev_close out does not undo it: the manager keeps
    // the last value of every field, and the row never falls back to zero.
    tick('SBIN', 'NSE', { ltp: 821 })
    expect(row(/^SBIN/)).toHaveTextContent('821.00+16.00 (+1.99%)')
  })

  it('greys a row whose ticks have stopped while its market is open, keeping sign and arrow', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { container } = renderPage()
    await screen.findByText('812.40')
    tick('SBIN', 'NSE', { ltp: 820 })
    expect(container.querySelector('[data-stale]')).toBeNull()

    await pass(STALE_MS + 1500)
    const stale = row(/^SBIN/)
    expect(stale.closest('li')).toHaveAttribute('data-stale', 'true')
    expect(stale).toHaveTextContent('+20.00 (+2.50%)')
    expect(stale.querySelector('[data-tick]')).toHaveAttribute('data-tick', 'up')
    expect(within(stale).getByText('Stale')).toHaveClass('sr-only')
    expect(statusLine(container)).toHaveAttribute('data-feed', 'stale')
    expect(statusLine(container)).toHaveTextContent(/^Stale · updated \d+s ago$/)
  })

  it('says the market is closed, and marks nothing stale, when it is', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    calendar.state.open = false
    const { container } = renderPage()
    await screen.findByText('812.40')

    await pass(STALE_MS + 5000)
    expect(container.querySelector('[data-stale]')).toBeNull()
    expect(statusLine(container)).toHaveTextContent('Market closed')
  })

  it('says Reconnecting with the age of the figures while the socket is down, never Real-time', async () => {
    const { container } = renderPage()
    await screen.findByRole('button', { name: /^SBIN/ })
    tick('SBIN', 'NSE', { ltp: 820 })
    expect(statusLine(container)).toHaveTextContent('Real-time')

    act(() =>
      feed.setState({
        connectionState: 'disconnected',
        isConnected: false,
        isAuthenticated: false,
        isFallbackMode: true,
      })
    )
    act(() => feed.push('SBIN', 'NSE', { ltp: 821 }, 'rest'))
    expect(row(/^SBIN/)).toHaveTextContent('821.00')
    expect(statusLine(container)).toHaveAttribute('data-feed', 'connecting')
    expect(statusLine(container)).toHaveTextContent(/^Reconnecting · updated \d+s ago$/)
  })

  it('subscribes only the open list in LTP mode, and closes it on a tab switch and on leaving', async () => {
    const user = userEvent.setup()
    const { unmount } = renderPage()
    await waitFor(() => expect(feed.live()).toEqual(['NSE:SBIN:LTP', 'NSE_INDEX:NIFTY:LTP']))

    await user.click(screen.getByRole('tab', { name: /Options/ }))
    await waitFor(() => expect(feed.live()).toEqual(['NFO:NIFTY28OCT2625000CE:LTP']))
    expect(feed.closed).toEqual(['NSE:SBIN:LTP', 'NSE_INDEX:NIFTY:LTP'])
    expect(localStorage.getItem(OPEN_LIST_KEY)).toBe('2')

    unmount()
    expect(feed.live()).toEqual([])
  })

  it('reads the snapshot again on a reconnect or a return after 15 s, never on a timer', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderPage()
    await screen.findByText('812.40')
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(1)

    await pass(5 * 60_000)
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(1)

    act(() => feed.setState({ connectionEpoch: 2 }))
    await settle()
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(2)

    setVisibility('hidden')
    await pass(5000)
    setVisibility('visible')
    await settle()
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(2)

    setVisibility('hidden')
    await pass(20_000)
    setVisibility('visible')
    await settle()
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(3)
  })
})

describe('Watchlist sheet', () => {
  it('opens Buy and Sell on the order ticket with the contract lot and tick size', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /^SBIN/ }))

    const sheet = screen.getByRole('dialog', { name: 'SBIN' })
    const buy = within(sheet).getByRole('button', { name: 'BUY' })
    await waitFor(() => expect(buy).toBeEnabled())
    expect(remote.contractOf).toHaveBeenCalledWith('key', 'SBIN', 'NSE')
    await user.click(buy)

    expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE lot 750 tick 0.05 Watchlist'
    )
  })

  it('links the chart and the option chain, and streams five levels of depth while open', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /^SBIN/ }))
    const sheet = screen.getByRole('dialog', { name: 'SBIN' })

    expect(within(sheet).getByRole('link', { name: 'Chart' })).toHaveAttribute(
      'href',
      '/chart/NSE/SBIN'
    )
    expect(await within(sheet).findByRole('link', { name: 'Option chain' })).toHaveAttribute(
      'href',
      '/optionchain?exchange=NFO&underlying=SBIN'
    )
    expect(remote.getUnderlyings).toHaveBeenCalledWith('NFO')

    expect(feed.live()).toContain('NSE:SBIN:Depth')
    tick('SBIN', 'NSE', {
      ltp: 812.5,
      depth: {
        buy: [
          { price: 812.45, quantity: 1200 },
          { price: 812.4, quantity: 300 },
        ],
        sell: [{ price: 812.55, quantity: 45000 }],
      },
    })
    const depth = within(sheet).getByRole('table')
    const first = within(depth).getAllByRole('row')[1]
    expect(first).toHaveTextContent('1,200812.45812.5545,000')

    // A frame with only the last price keeps the book it does not mention.
    tick('SBIN', 'NSE', { ltp: 812.55 })
    expect(within(depth).getAllByRole('row')[1]).toHaveTextContent('1,200812.45812.5545,000')

    await user.click(within(sheet).getByRole('button', { name: 'Close' }))
    expect(feed.closed).toContain('NSE:SBIN:Depth')
    expect(feed.live()).toContain('NSE:SBIN:LTP')
  })

  it('opens the chart route for the instrument', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /^SBIN/ }))
    await user.click(screen.getByRole('link', { name: 'Chart' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/chart/NSE/SBIN')
  })

  it('gives an index its chart and option chain, but no ticket and no depth', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /^NIFTY/ }))
    const sheet = screen.getByRole('dialog', { name: 'NIFTY' })

    expect(await within(sheet).findByRole('link', { name: 'Option chain' })).toHaveAttribute(
      'href',
      '/optionchain?exchange=NFO&underlying=NIFTY'
    )
    expect(within(sheet).queryByRole('button', { name: 'BUY' })).toBeNull()
    expect(within(sheet).queryByRole('table')).toBeNull()
    expect(feed.live()).not.toContain('NSE_INDEX:NIFTY:Depth')
  })

  it('has no option chain for a share without options', async () => {
    const user = userEvent.setup()
    remote.getUnderlyings.mockResolvedValue({ status: 'success', underlyings: ['NIFTY'] })
    renderPage()
    await user.click(await screen.findByRole('button', { name: /^SBIN/ }))
    const sheet = screen.getByRole('dialog', { name: 'SBIN' })

    await waitFor(() => expect(remote.getUnderlyings).toHaveBeenCalled())
    await settle()
    expect(within(sheet).queryByRole('link', { name: 'Option chain' })).toBeNull()
  })

  it('takes the instrument out of the list', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /^SBIN/ }))
    await user.click(screen.getByRole('button', { name: 'Remove from Main' }))

    expect(server.api.removeItem).toHaveBeenCalledWith(1, 11)
    expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull()
    await waitFor(() => expect(screen.queryByRole('button', { name: /^SBIN/ })).toBeNull())
  })
})
