/**
 * The phone watchlist, end to end in jsdom. The lists API is an in-memory
 * stand-in for blueprints/watchlist.py, the market-data manager is the shared
 * test harness (so subscriptions are real, and a line is emptied once nothing
 * subscribes to it, as the manager does), frames run by hand, and the order
 * ticket is a stub that shows what it was opened with. The query client is the
 * app's own (`createQueryClient`), so cache ages are the ones the owner gets.
 * What is pinned is what the owner sees and what the page asks of the server.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Profiler } from 'react'
import {
  type InitialEntry,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router'
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
  const state = { lists: [] as List[], nextId: 1000, reorderDelay: 0 }
  /** What the server did, in order. */
  const log: string[] = []
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
      log.push(`remove ${itemId}`)
      const list = find(id)
      list.items = list.items.filter((item) => item.id !== itemId)
    }),
    reorderItems: vi.fn(async (id: number, order: number[]) => {
      log.push(`reorder ${order.join(',')} sent`)
      if (state.reorderDelay) await new Promise((done) => setTimeout(done, state.reorderDelay))
      const list = find(id)
      list.items = order
        .map((itemId) => list.items.find((item) => item.id === itemId))
        .filter((item): item is Item => item !== undefined)
        .map((item, position) => ({ ...item, position }))
      log.push(`reorder ${order.join(',')} saved`)
    }),
  }
  return { state, api, log, refuse }
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
// that the sheet opens it, for the right instrument, side, contract and product.
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
              'Cancel order'
            )
          )
        : null,
  }
})

// The list tabs render with the page and not with its live part: how often
// they render is how often the page around the list does.
const shell = vi.hoisted(() => ({ renders: 0 }))
vi.mock('@/sudarshan/watchlist/ListTabs', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/sudarshan/watchlist/ListTabs')>()
  return {
    ...real,
    ListTabs: (props: Parameters<typeof real.ListTabs>[0]) => {
      shell.renders += 1
      return real.ListTabs(props)
    },
  }
})

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { STALE_MS } from '@/sudarshan/dashboard/live'
import { STORAGE_KEY as PRODUCT_KEY } from '@/sudarshan/ticket/productPref'
import { LISTS_FULL } from '@/sudarshan/watchlist/ListTabs'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import Watchlist, { LIST_FULL, OPEN_LIST_KEY } from './Watchlist'

// Each test here is a whole flow in jsdom (a sheet, a search, a ticket, a list
// of 250 rows); on a busy machine one can pass five seconds with nothing wrong.
vi.setConfig({ testTimeout: 15_000 })

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
  'NSE:ICICIBANK': [1420, 1410],
  'NSE:INFY': [1500, 1490],
  'CDS:USDINR28OCT26FUT': [83.2525, 83.1],
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

/** Where the router is: path and query. */
function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}

/** The phone's Back, as the router has it. */
const history = { go: (_delta: number) => {} }
function Nav() {
  const navigate = useNavigate()
  history.go = (delta) => navigate(delta)
  return null
}

function ChartProbe() {
  const { exchange, symbol } = useParams()
  return <p>{`Chart of ${symbol} on ${exchange}`}</p>
}

/** Commits of the page's tree, from React's own profiler. */
let commits = 0

function renderPage(entries: InitialEntry[] = ['/watchlist']) {
  const client = createQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Nav />
        <Profiler
          id="page"
          onRender={() => {
            commits += 1
          }}
        >
          <Routes>
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/dashboard" element={<p>Dashboard page</p>} />
            <Route path="/chart/:exchange/:symbol" element={<ChartProbe />} />
            <Route path="*" element={null} />
          </Routes>
        </Profiler>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

/** Run one animation frame. */
function frame() {
  act(() => frames.flush())
}

/** Let promises settle inside React's act, and draw what they brought. */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
  frame()
}

/** Move the fake clock on, running every timer due on the way, then draw. */
async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
  frame()
}

/** One tick, drawn on the next frame. */
function tick(
  symbol: string,
  exchange: string,
  data: Record<string, unknown>,
  source: 'websocket' | 'rest' = 'websocket'
) {
  act(() => {
    feed.push(symbol, exchange, data, source)
    frames.flush()
  })
}

function back() {
  act(() => history.go(-1))
}

const row = (name: RegExp) => screen.getByRole('button', { name })
const arrow = (name: RegExp) => row(name).querySelector('[data-tick]')
const statusLine = (container: HTMLElement) => container.querySelector('[data-feed]')
const where = () => screen.getByTestId('where')
const socketDown = () =>
  act(() =>
    feed.setState({ connectionState: 'disconnected', isConnected: false, isAuthenticated: false })
  )

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

/** The sheet of a row, opened by a tap. */
async function openSheet(user: ReturnType<typeof userEvent.setup>, name: RegExp, title: string) {
  await user.click(await screen.findByRole('button', { name }))
  return screen.getByRole('dialog', { name: title })
}

const DEPTH = {
  buy: [
    { price: 812.45, quantity: 1200 },
    { price: 812.4, quantity: 300 },
  ],
  sell: [{ price: 812.55, quantity: 45000 }],
}

beforeEach(() => {
  feed.reset()
  frames.install()
  commits = 0
  shell.renders = 0
  localStorage.clear()
  server.state.lists = JSON.parse(JSON.stringify([MAIN, OPTIONS]))
  server.state.nextId = 1000
  server.state.reorderDelay = 0
  server.log.length = 0
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
  vi.unstubAllGlobals()
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
    expect(where()).toHaveTextContent('/watchlist?sheet=new')
    await user.type(screen.getByRole('textbox', { name: 'List name' }), 'Banks')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(server.api.create).toHaveBeenCalledWith('Banks')
    expect(await screen.findByRole('tab', { name: /Banks/ })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(localStorage.getItem(OPEN_LIST_KEY)).toBe('1000')
    await waitFor(() => expect(where()).toHaveTextContent(/^\/watchlist$/))
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

  it("deletes the open list after saying what goes with it, on the app's own cache ages", async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })

    await openMenu(user, 'Delete list')
    const dialog = screen.getByRole('dialog', { name: 'Delete “Main”?' })
    expect(dialog).toHaveTextContent('Its 2 symbols go with it.')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
    expect(server.api.remove).toHaveBeenCalledWith(1)
    // While the dialog is open the tabs are hidden from these queries: wait for it first.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() =>
      expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Options1'])
    )
    expect(screen.getByRole('tab', { name: /Options/ })).toHaveAttribute('aria-selected', 'true')
    expect(where()).toHaveTextContent(/^\/watchlist$/)
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

  it("keeps every other row's level when a symbol is added", async () => {
    const user = userEvent.setup()
    remote.searchSymbols.mockResolvedValue([{ symbol: 'HDFCBANK', exchange: 'NSE', name: '' }])
    renderPage()
    await screen.findByRole('button', { name: /^SBIN/ })
    tick('SBIN', 'NSE', { ltp: 820 })
    expect(row(/^SBIN/)).toHaveTextContent('820.00')

    await user.click(screen.getByRole('button', { name: 'Add' }))
    const sheet = screen.getByRole('dialog', { name: 'Add to Main' })
    await user.type(within(sheet).getByRole('searchbox', { name: 'Search symbols' }), 'HDFC')
    await user.click(await within(sheet).findByRole('button', { name: 'Add HDFCBANK, NSE' }))
    await user.click(within(sheet).getByRole('button', { name: 'Close' }))
    await screen.findByRole('button', { name: /^HDFCBANK/ })
    frame()

    // SBIN was not subscribed again (that would have emptied its line), and
    // its level is the tick's, not the snapshot's 812.40.
    expect(feed.opened.filter((key) => key === 'NSE:SBIN:LTP')).toHaveLength(1)
    expect(row(/^SBIN/)).toHaveTextContent('820.00')
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

  it('saves edits one after another, so the screen ends as the server does', async () => {
    const user = userEvent.setup()
    server.state.lists[0].items.push({ id: 13, symbol: 'INFY', exchange: 'NSE', position: 2 })
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })
    await openMenu(user, 'Reorder or remove')

    // A remove tapped while a slow move is still being saved.
    server.state.reorderDelay = 300
    await user.click(screen.getByRole('button', { name: 'Move SBIN down' }))
    await user.click(screen.getByRole('button', { name: 'Remove INFY' }))
    const reads = server.api.list.mock.calls.length

    await waitFor(() => expect(server.log).toContain('remove 13'))
    expect(server.log).toEqual(['reorder 12,11,13 sent', 'reorder 12,11,13 saved', 'remove 13'])
    await waitFor(() => expect(server.api.list).toHaveBeenCalledTimes(reads + 1))
    const order = () =>
      screen
        .getAllByRole('button', { name: /^Move .* up$/ })
        .map((button) => button.getAttribute('aria-label'))
    expect(order()).toEqual(['Move NIFTY up', 'Move SBIN up'])
    expect(server.state.lists[0].items.map((item) => item.symbol)).toEqual(['NIFTY', 'SBIN'])
  })

  it('puts a removed symbol back when the server refuses', async () => {
    const user = userEvent.setup()
    let refuse = () => {}
    server.api.removeItem.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          refuse = () => reject(server.refuse('List not found'))
        })
    )
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })
    await openMenu(user, 'Reorder or remove')

    await user.click(screen.getByRole('button', { name: 'Remove SBIN' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove SBIN' })).toBeNull())
    await act(async () => refuse())
    expect(await screen.findByRole('button', { name: 'Remove SBIN' })).toBeInTheDocument()
  })

  it('puts the order back when a move is refused', async () => {
    const user = userEvent.setup()
    let refuse = () => {}
    server.api.reorderItems.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          refuse = () => reject(new Error('offline'))
        })
    )
    renderPage()
    await screen.findByRole('tab', { name: /Main/ })
    await openMenu(user, 'Reorder or remove')
    const first = () => screen.getAllByRole('button', { name: /^Move .* down$/ })[0]

    await user.click(screen.getByRole('button', { name: 'Move SBIN down' }))
    await waitFor(() => expect(first()).toHaveAccessibleName('Move NIFTY down'))
    await act(async () => refuse())
    await waitFor(() => expect(first()).toHaveAccessibleName('Move SBIN down'))
  })

  it('turns New list off at the cap of lists, and says why', async () => {
    const user = userEvent.setup()
    server.state.lists = Array.from({ length: 50 }, (_, index) => ({
      id: index + 1,
      name: `List ${index + 1}`,
      position: index,
      items: [],
    }))
    renderPage()
    expect(await screen.findAllByRole('tab')).toHaveLength(50)

    await user.click(screen.getByRole('button', { name: 'List options' }))
    const item = await screen.findByRole('menuitem', { name: 'New list' })
    expect(item).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText(LISTS_FULL)).toBeInTheDocument()
  })

  // The two below hold a full list of 250 rows: they find things by text, a
  // role query over that many rows costs a second or more each time it polls.
  it('turns Add off for a full list, and says why', async () => {
    server.state.lists[0].items = Array.from({ length: 250 }, (_, index) => ({
      id: 5000 + index,
      symbol: `S${index}`,
      exchange: 'NSE',
      position: index,
    }))
    renderPage()

    const reason = await screen.findByText(LIST_FULL)
    const add = screen.getByText('Add', { selector: 'button' })
    expect(add).toBeDisabled()
    expect(add).toHaveAttribute('aria-describedby', reason.id)
  })

  it('stops taking symbols in the search once the list is full', async () => {
    const user = userEvent.setup()
    server.state.lists[0].items = Array.from({ length: 249 }, (_, index) => ({
      id: 5000 + index,
      symbol: `S${index}`,
      exchange: 'NSE',
      position: index,
    }))
    remote.searchSymbols.mockResolvedValue([
      { symbol: 'HDFCBANK', exchange: 'NSE', name: '' },
      { symbol: 'ICICIBANK', exchange: 'NSE', name: '' },
    ])
    renderPage()
    await screen.findByText('249')

    await user.click(screen.getByText('Add', { selector: 'button' }))
    const sheet = screen.getByText('Add to Main').closest('[role="dialog"]') as HTMLElement
    await user.type(within(sheet).getByLabelText('Search symbols'), 'BANK')
    await user.click(await within(sheet).findByRole('button', { name: 'Add HDFCBANK, NSE' }))

    expect(await within(sheet).findByText(/Main holds 250 symbols/)).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Add ICICIBANK, NSE' })).toBeDisabled()
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
    // The first tick is a level; a direction needs a move between two ticks.
    expect(arrow(/^SBIN/)).toBeNull()
    expect(statusLine(container)).toHaveTextContent('Real-time')

    tick('SBIN', 'NSE', { ltp: 825 })
    expect(arrow(/^SBIN/)).toHaveAttribute('data-tick', 'up')

    tick('SBIN', 'NSE', { ltp: 815.5 })
    expect(row(/^SBIN/)).toHaveTextContent('815.50+15.50 (+1.94%)')
    expect(arrow(/^SBIN/)).toHaveAttribute('data-tick', 'down')

    // A tick at the same price keeps the last direction.
    tick('SBIN', 'NSE', { ltp: 815.5 })
    expect(arrow(/^SBIN/)).toHaveAttribute('data-tick', 'down')

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

  it('shows a currency contract to four decimals', async () => {
    server.state.lists.push({
      id: 3,
      name: 'FX',
      position: 2,
      items: [{ id: 31, symbol: 'USDINR28OCT26FUT', exchange: 'CDS', position: 0 }],
    })
    localStorage.setItem(OPEN_LIST_KEY, '3')
    renderPage()

    expect(await screen.findByRole('button', { name: /^USDINR FUT/ })).toHaveTextContent(
      '83.2525+0.1525 (+0.18%)'
    )
    tick('USDINR28OCT26FUT', 'CDS', { ltp: 83.255 })
    expect(row(/^USDINR FUT/)).toHaveTextContent('83.2550+0.1550 (+0.19%)')
  })

  it('greys a row whose level has stopped while the socket is down, keeping sign and arrow', async () => {
    fakeClock()
    const { container } = renderPage()
    await screen.findByText('812.40')
    tick('SBIN', 'NSE', { ltp: 815 })
    tick('SBIN', 'NSE', { ltp: 820 })
    expect(container.querySelector('[data-stale]')).toBeNull()

    socketDown()
    await pass(STALE_MS + 1500)
    const stale = row(/^SBIN/)
    expect(stale.closest('li')).toHaveAttribute('data-stale', 'true')
    expect(stale).toHaveTextContent('+20.00 (+2.50%)')
    expect(stale.querySelector('[data-tick]')).toHaveAttribute('data-tick', 'up')
    expect(within(stale).getByText('Stale')).toHaveClass('sr-only')
    expect(statusLine(container)).toHaveAttribute('data-feed', 'connecting')
    expect(statusLine(container)).toHaveTextContent(/^Reconnecting · updated \d+s ago$/)
  })

  it('keeps a quiet row out of Stale while the socket is up, and reads it again after a minute', async () => {
    fakeClock()
    const { container } = renderPage()
    await screen.findByText('812.40')
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(1)

    // SBIN trades every second; NIFTY, read once, never ticks.
    for (let second = 0; second < 65; second += 1) {
      tick('SBIN', 'NSE', { ltp: 815 + (second % 2) / 20 })
      await pass(1000)
    }
    expect(row(/^NIFTY/).closest('li')).not.toHaveAttribute('data-stale')
    expect(statusLine(container)).toHaveTextContent('Real-time')
    // One REST read for the quiet row alone, about a minute in.
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(2)
    expect(remote.getMultiQuotes).toHaveBeenLastCalledWith('key', [
      { symbol: 'NIFTY', exchange: 'NSE_INDEX' },
    ])

    // Then the whole list goes quiet: its age, not Stale, and no row grey.
    await pass(20_000)
    expect(statusLine(container)).toHaveAttribute('data-feed', 'delayed')
    expect(statusLine(container)).toHaveTextContent(/^Updated \d+s ago$/)
    expect(container.querySelector('[data-stale]')).toBeNull()
  })

  it('says the market is closed, and marks nothing stale, when it is', async () => {
    fakeClock()
    calendar.state.open = false
    const { container } = renderPage()
    await screen.findByText('812.40')

    socketDown()
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
    tick('SBIN', 'NSE', { ltp: 821 }, 'rest')
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

  it('never re-reads a list whose ticks are coming; reads again on a reconnect or a return after 15 s', async () => {
    fakeClock()
    renderPage()
    await screen.findByText('812.40')
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(1)

    for (let second = 0; second < 130; second += 1) {
      tick('SBIN', 'NSE', { ltp: 815 + (second % 2) / 20 })
      tick('NIFTY', 'NSE_INDEX', { ltp: 25_880 + (second % 2) })
      await pass(1000)
    }
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

  it('reads the snapshot again when a market opens or closes', async () => {
    fakeClock()
    const { container } = renderPage()
    await screen.findByText('812.40')
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(1)

    calendar.state.open = false
    await pass(1500)
    await settle()
    expect(statusLine(container)).toHaveTextContent('Market closed')
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(2)

    calendar.state.open = true
    await pass(1500)
    await settle()
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(3)
  })

  it('says a failed read failed, with Try again, even after an earlier one worked', async () => {
    fakeClock()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await screen.findByText('812.40')

    remote.getMultiQuotes.mockRejectedValueOnce(new Error('offline'))
    await pass(6000)
    act(() => feed.setState({ connectionEpoch: 2 }))
    await settle()
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(2)
    const notice = await screen.findByText('Prices could not be refreshed just now.')
    // The levels already on screen stay.
    expect(row(/^SBIN/)).toHaveTextContent('812.40')

    await user.click(within(notice).getByRole('button', { name: 'Try again' }))
    await settle()
    expect(remote.getMultiQuotes).toHaveBeenCalledTimes(3)
    expect(screen.queryByText('Prices could not be refreshed just now.')).toBeNull()
  })

  it('draws a burst of ticks once per frame, and leaves the page around the list alone', async () => {
    renderPage()
    await screen.findByText('812.40')
    frame()
    const page = shell.renders
    const before = commits

    act(() => {
      for (let i = 0; i < 30; i += 1) {
        if (i % 2 === 0) feed.push('NIFTY', 'NSE_INDEX', { ltp: 25_800 + i })
        else feed.push('SBIN', 'NSE', { ltp: 800 + i })
      }
    })
    expect(commits).toBe(before)

    frame()
    expect(commits).toBe(before + 1)
    expect(shell.renders).toBe(page)
    expect(row(/^SBIN/)).toHaveTextContent('829.00')
    expect(row(/^NIFTY/)).toHaveTextContent('25,828.00')
  })
})

describe('Watchlist sheet', () => {
  it('keeps Buy and Sell shut until the lot size is read, then opens the ticket with it', async () => {
    const user = userEvent.setup()
    let read = (_contract: { lotSize: number; tickSize: number }) => {}
    remote.contractOf.mockReturnValue(
      new Promise((done) => {
        read = done
      })
    )
    renderPage()
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')
    expect(where()).toHaveTextContent('/watchlist?item=11')

    const buy = within(sheet).getByRole('button', { name: 'BUY' })
    const sell = within(sheet).getByRole('button', { name: 'SELL' })
    expect(buy).toBeDisabled()
    expect(sell).toBeDisabled()
    expect(within(sheet).getByText('Reading the lot size.')).toBeInTheDocument()
    await user.click(buy)
    expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull()

    await act(async () => read({ lotSize: 750, tickSize: 0.05 }))
    await waitFor(() => expect(buy).toBeEnabled())
    expect(remote.contractOf).toHaveBeenCalledWith('key', 'SBIN', 'NSE')
    await user.click(buy)

    expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE lot 750 tick 0.05 product default Watchlist'
    )
    expect(where()).toHaveTextContent('/watchlist?sheet=order')
  })

  it('keeps the ticket shut when the lot size cannot be read, and offers to try again', async () => {
    const user = userEvent.setup()
    remote.contractOf.mockRejectedValueOnce(new Error('offline'))
    renderPage()
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')

    expect(await within(sheet).findByText(/The lot size could not be read/)).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'BUY' })).toBeDisabled()
    await user.click(within(sheet).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(within(sheet).getByRole('button', { name: 'BUY' })).toBeEnabled())
  })

  it('opens the ticket with the product last picked for that segment on this device', async () => {
    const user = userEvent.setup()
    renderPage()
    let sheet = await openSheet(user, /^SBIN/, 'SBIN')
    let pick = within(sheet).getByRole('group', { name: 'The ticket opens with' })
    expect(within(pick).getByRole('button', { name: /^CNC/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    await user.click(within(pick).getByRole('button', { name: /^MIS/ }))
    expect(within(pick).getByRole('button', { name: /^MIS/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    const buy = within(sheet).getByRole('button', { name: 'BUY' })
    await waitFor(() => expect(buy).toBeEnabled())
    await user.click(buy)
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent('product MIS')
    expect(JSON.parse(localStorage.getItem(PRODUCT_KEY) ?? '{}')).toEqual({ cash: 'MIS' })
    await user.click(screen.getByRole('button', { name: 'Cancel order' }))

    // The cash pick is the cash segment's; F&O keeps the ticket's own default.
    await user.click(await screen.findByRole('tab', { name: /Options/ }))
    sheet = await openSheet(user, /^NIFTY 25000 CE/, 'NIFTY 25000 CE')
    pick = within(sheet).getByRole('group', { name: 'The ticket opens with' })
    expect(within(pick).getByRole('button', { name: /^NRML/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    const sell = within(sheet).getByRole('button', { name: 'SELL' })
    await waitFor(() => expect(sell).toBeEnabled())
    await user.click(sell)
    expect(screen.getByRole('region', { name: 'Order ticket' })).toHaveTextContent(
      'SELL NIFTY28OCT2625000CE NFO lot 750 tick 0.05 product default Watchlist'
    )
  })

  it('links the chart and the option chain, and streams five levels of depth while open', async () => {
    const user = userEvent.setup()
    renderPage()
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')

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
    expect(within(sheet).getByText('Waiting for market depth.')).toBeInTheDocument()
    tick('SBIN', 'NSE', { ltp: 812.5, depth: DEPTH })
    const depth = within(sheet).getByRole('table')
    expect(within(depth).getAllByRole('row')[1]).toHaveTextContent('1,200812.45812.5545,000')

    // A frame with only the last price keeps the book it does not mention.
    tick('SBIN', 'NSE', { ltp: 812.55 })
    expect(within(depth).getAllByRole('row')[1]).toHaveTextContent('1,200812.45812.5545,000')

    await user.click(within(sheet).getByRole('button', { name: 'Close' }))
    expect(feed.closed).toContain('NSE:SBIN:Depth')
    expect(feed.live()).toContain('NSE:SBIN:LTP')
  })

  it('shows a book from an earlier visit only once a new one arrives', async () => {
    const user = userEvent.setup()
    renderPage()
    let sheet = await openSheet(user, /^SBIN/, 'SBIN')
    tick('SBIN', 'NSE', { ltp: 812.5, depth: DEPTH })
    expect(within(sheet).getByRole('table')).toHaveTextContent('812.45')
    await user.click(within(sheet).getByRole('button', { name: 'Close' }))

    // The row keeps the line streaming in LTP; its old book stays on it.
    tick('SBIN', 'NSE', { ltp: 830 })
    sheet = await openSheet(user, /^SBIN/, 'SBIN')
    // A frame passes: the sheet has the line, old book and all, and still waits.
    frame()
    expect(feed.cache.get('NSE:SBIN')?.data.depth).toBe(DEPTH)
    expect(within(sheet).queryByRole('table')).toBeNull()
    expect(within(sheet).getByText('Waiting for market depth.')).toBeInTheDocument()

    tick('SBIN', 'NSE', {
      ltp: 830.05,
      depth: { buy: [{ price: 830, quantity: 75 }], sell: [{ price: 830.1, quantity: 150 }] },
    })
    expect(within(within(sheet).getByRole('table')).getAllByRole('row')[1]).toHaveTextContent(
      '75830.00830.10150'
    )
  })

  it('says the market is closed instead of waiting for a book', async () => {
    const user = userEvent.setup()
    calendar.state.open = false
    renderPage()
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')
    expect(within(sheet).getByText('Market closed. Depth shows when it opens.')).toBeInTheDocument()

    // A book that does come (the broker's snapshot on subscribing) is shown.
    tick('SBIN', 'NSE', { ltp: 812.5, depth: DEPTH })
    expect(within(sheet).getByRole('table')).toHaveTextContent('812.45')
  })

  it('ages the book from its last change, and calls it stale once the socket is down', async () => {
    fakeClock()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')
    tick('SBIN', 'NSE', { ltp: 812.5, depth: DEPTH })

    // The last price keeps moving; the book does not.
    for (let second = 0; second < 12; second += 1) {
      await pass(1000)
      tick('SBIN', 'NSE', { ltp: 812.5 + (second % 2) / 20 })
    }
    const age = () => sheet.querySelector('[data-depth-age]')
    expect(age()).toHaveAttribute('data-depth-age', 'old')
    expect(age()).toHaveTextContent(/^Updated 1[12]s ago$/)
    expect(within(sheet).getByRole('table')).not.toHaveAttribute('data-stale')

    socketDown()
    frame()
    expect(age()).toHaveAttribute('data-depth-age', 'stale')
    expect(age()).toHaveTextContent(/^Stale · updated 1[12]s ago$/)
    expect(within(sheet).getByRole('table')).toHaveAttribute('data-stale', 'true')
  })

  it("says how the sheet's own instrument stands, not the list's", async () => {
    const user = userEvent.setup()
    const { container } = renderPage()
    await screen.findByRole('button', { name: /^NIFTY/ })
    tick('SBIN', 'NSE', { ltp: 815 })
    expect(statusLine(container)).toHaveTextContent('Real-time')

    const sheet = await openSheet(user, /^NIFTY/, 'NIFTY')
    // NIFTY has its snapshot and no tick yet.
    expect(sheet.querySelector('[data-feed]')).toHaveTextContent(/^Updated \d+s ago$/)
  })

  it('opens the chart screen for the instrument', async () => {
    const user = userEvent.setup()
    renderPage()
    await openSheet(user, /^SBIN/, 'SBIN')
    await user.click(screen.getByRole('link', { name: 'Chart' }))
    expect(await screen.findByText('Chart of SBIN on NSE')).toBeInTheDocument()
    expect(where()).toHaveTextContent('/chart/NSE/SBIN')
  })

  it('gives an index its chart and option chain, but no ticket and no depth', async () => {
    const user = userEvent.setup()
    renderPage()
    const sheet = await openSheet(user, /^NIFTY/, 'NIFTY')

    expect(await within(sheet).findByRole('link', { name: 'Option chain' })).toHaveAttribute(
      'href',
      '/optionchain?exchange=NFO&underlying=NIFTY'
    )
    expect(within(sheet).queryByRole('button', { name: 'BUY' })).toBeNull()
    expect(within(sheet).queryByRole('table')).toBeNull()
    expect(within(sheet).queryByRole('group', { name: 'The ticket opens with' })).toBeNull()
    expect(feed.live()).not.toContain('NSE_INDEX:NIFTY:Depth')
  })

  it('has no option chain for a share without options', async () => {
    const user = userEvent.setup()
    remote.getUnderlyings.mockResolvedValue({ status: 'success', underlyings: ['NIFTY'] })
    renderPage()
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')

    await waitFor(() => expect(remote.getUnderlyings).toHaveBeenCalled())
    await settle()
    expect(within(sheet).queryByRole('link', { name: 'Option chain' })).toBeNull()
  })

  it('takes the instrument out of the list', async () => {
    const user = userEvent.setup()
    renderPage(['/dashboard', '/watchlist'])
    await openSheet(user, /^SBIN/, 'SBIN')
    await user.click(screen.getByRole('button', { name: 'Remove from Main' }))

    expect(server.api.removeItem).toHaveBeenCalledWith(1, 11)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull())
    await waitFor(() => expect(screen.queryByRole('button', { name: /^SBIN/ })).toBeNull())
    expect(where()).toHaveTextContent(/^\/watchlist$/)
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })
})

describe('Watchlist and the Back button', () => {
  it('closes the sheet on Back and stays on the watchlist', async () => {
    const user = userEvent.setup()
    renderPage(['/dashboard', '/watchlist'])
    await openSheet(user, /^SBIN/, 'SBIN')

    back()
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull())
    expect(where()).toHaveTextContent(/^\/watchlist$/)
    // The rows stream on; only the sheet's depth was let go.
    expect(feed.live()).toEqual(['NSE:SBIN:LTP', 'NSE_INDEX:NIFTY:LTP'])

    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('leaves the same history when the sheet is closed by its button', async () => {
    const user = userEvent.setup()
    renderPage(['/dashboard', '/watchlist'])
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')

    await user.click(within(sheet).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(where()).toHaveTextContent(/^\/watchlist$/))
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('cancels the ticket on Back, back to the list rather than the sheet', async () => {
    const user = userEvent.setup()
    renderPage(['/dashboard', '/watchlist'])
    const sheet = await openSheet(user, /^SBIN/, 'SBIN')
    const buy = within(sheet).getByRole('button', { name: 'BUY' })
    await waitFor(() => expect(buy).toBeEnabled())
    await user.click(buy)
    expect(screen.getByRole('region', { name: 'Order ticket' })).toBeInTheDocument()

    back()
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull())
    expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull()
    expect(where()).toHaveTextContent(/^\/watchlist$/)
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('closes the search and a list dialog on Back', async () => {
    const user = userEvent.setup()
    renderPage(['/dashboard', '/watchlist'])
    await screen.findByRole('tab', { name: /Main/ })

    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getByRole('dialog', { name: 'Add to Main' })).toBeInTheDocument()
    back()
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add to Main' })).toBeNull())

    await openMenu(user, 'Delete list')
    expect(screen.getByRole('dialog', { name: 'Delete “Main”?' })).toBeInTheDocument()
    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(server.api.remove).not.toHaveBeenCalled()
    expect(where()).toHaveTextContent(/^\/watchlist$/)
  })

  it('closes a sheet it was linked to in place, so Back still leaves for the page before', async () => {
    const user = userEvent.setup()
    renderPage(['/dashboard', '/watchlist?item=11'])
    const sheet = await screen.findByRole('dialog', { name: 'SBIN' })

    await user.click(within(sheet).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(where()).toHaveTextContent(/^\/watchlist$/))
    back()
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
  })

  it('drops what it cannot show: a symbol not in the list, a ticket with no order', async () => {
    renderPage(['/watchlist?item=999'])
    await screen.findByRole('tab', { name: /Main/ })
    await waitFor(() => expect(where()).toHaveTextContent(/^\/watchlist$/))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('does not reopen a ticket with the page: a ticket opens only from a tap', async () => {
    const order = {
      item: MAIN.items[0],
      action: 'BUY',
      contract: { lotSize: 750, tickSize: 0.05 },
      product: 'MIS',
    }
    renderPage([
      '/watchlist',
      { pathname: '/watchlist', search: '?sheet=order', state: { sdOverlay: true, order } },
    ])
    await screen.findByRole('tab', { name: /Main/ })
    await waitFor(() => expect(where()).toHaveTextContent(/^\/watchlist$/))
    expect(screen.queryByRole('region', { name: 'Order ticket' })).toBeNull()
  })
})
