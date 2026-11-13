/**
 * F10 on the page: built-in lists (M8.8). The All lists group with a known and an unknown count and
 * the note; a built-in list with its lock, the server's lines once each, no edit controls and the
 * things that still work on it (sort, flags, notes); Make a copy (every key in the order shown, the
 * server's words, the new list opened, the 500 ms guard); the master-not-loaded and gone sentences;
 * "Alert on this list" with no count; the switch off. The bridge is the v2 mock; the lists API and
 * the quote read are in-memory stand-ins.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<
    typeof import('@/sudarshan/watchlist/v2/mockBridge').createBridge
  >,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('@/sudarshan/watchlist/v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

const own = vi.hoisted(() => ({
  lists: [] as Array<{
    id: number
    name: string
    position: number
    items: Array<{ id: number; symbol: string; exchange: string; position: number }>
  }>,
}))
vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: { list: vi.fn(async () => own.lists) },
}))
const calendar = vi.hoisted(() => ({
  value: {
    isLoading: false,
    error: null,
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
  },
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar.value }))
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: vi.fn(async () => ({ status: 'success', results: [] })) },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  searchSymbols: vi.fn(),
  contractOf: vi.fn(async () => ({ lotSize: 1, tickSize: 0.05 })),
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: vi.fn(async () => ({ status: 'success', underlyings: [] })) },
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/toast')>()),
  toast: toasts,
}))

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import type { Row, View } from '@/sudarshan/watchlist/v2/types'
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const row = (symbol: string, flag: number | null = null): Row => ({
  item: null,
  contract: {
    exchange: 'NSE',
    symbol,
    key: `NSE:${symbol}`,
    name: symbol,
    lot: null,
    tick: null,
    expiry: null,
    isin: null,
  },
  section_id: null,
  flag,
  has_note: false,
  expired: false,
  metric: null,
})

const LOCKED = 'Built-in list, locked. F&O stocks: 3 symbols.'
const SOURCE =
  'From the Upstox contract list of 9 Nov 2026: every stock with futures. Updated each morning after the day’s login.'
const MISSING = '1 stock has futures but no stock listing today: ABC.'
const WINDOW =
  'Prices update for the 120 rows around what you see; the rest show their last price and its time.'
const SYMBOLS = ['RELIANCE', 'TCS', 'INFY']
const MANY = Array.from({ length: 300 }, (_, index) => `S${String(index).padStart(3, '0')}`)

const names = ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'].map((name, i) => ({
  mark: i + 1,
  name,
}))
const entry = (ref: string, name: string, count: number | null, note: string | null) => ({
  ref,
  kind: 'preset',
  name,
  count,
  room: null,
  locked: true,
  note,
})

/** What the server last saved as the View of a list: `view` answers it back. */
const saved = { pref: { sort: 'list', descending: false, basis: 'close', held: false } }

function viewFor(ref: string): Partial<View> {
  const long = ref === 'preset:big'
  const symbols = long ? MANY : SYMBOLS
  return {
    ref,
    kind: 'preset',
    name: long ? 'Big list' : 'F&O stocks',
    version: '0',
    locked: true,
    as_of: '2026-11-09',
    lines: long
      ? ['Built-in list, locked. Big list: 300 symbols.', SOURCE, WINDOW, SOURCE]
      : [LOCKED, SOURCE, MISSING],
    total: symbols.length,
    missing: ['NFO:ABC'],
    sections: [],
    rows: symbols.map((symbol) => row(symbol)),
    follow: [],
    pref: saved.pref,
  } as Partial<View>
}

function Where() {
  const at = useLocation()
  return <p data-testid="where">{`${at.pathname}${at.search}`}</p>
}

function mount(entry = '/watchlist?sheet=lists') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/market-alerts" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

async function openBuiltIn(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  const block = await screen.findByRole('region', { name: 'Built-in (locked)' })
  await user.click(within(block).getByRole('button', { name }))
}

const state = (favourites: unknown[] = [], enabled = true) => ({
  enabled,
  versions: { lists: 'a:1', favourites: 1, flags: 1 },
  flag_names: names,
  favourites,
})

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  toasts.success.mockClear()
  useAuthStore.setState({ apiKey: 'key' })
  own.lists = [
    {
      id: 1,
      name: 'Main',
      position: 0,
      items: [{ id: 11, symbol: 'SBIN', exchange: 'NSE', position: 0 }],
    },
  ]
  b().on('state', () => state())
  b().on('lists', () => ({
    version: 'a:1',
    own: [
      { ref: 'list:1', kind: 'list', name: 'Main', count: 1, room: 249, locked: false, note: null },
    ],
    presets: [
      entry('preset:fno_stocks', 'F&O stocks', 3, 'As of 9 Nov 2026'),
      entry('preset:indices', 'Indices', null, null),
      entry('preset:big', 'Big list', 300, 'As of 9 Nov 2026'),
    ],
    ranked: [],
    flags: [],
    favourites: [],
  }))
  b().on('view', (call) => viewFor((call.params as { ref: string }).ref))
  saved.pref = { sort: 'list', descending: false, basis: 'close', held: false }
  b().on('pref', (call) => {
    const { sort, descending, basis, held } = call.body as typeof saved.pref
    saved.pref = { sort, descending, basis, held }
    return { version: '1' }
  })
  b().on('symbol', () => ({
    key: 'NSE:TCS',
    flag: null,
    flag_name: null,
    note: null,
    note_at: null,
    expired: false,
    version: null,
    lists: [],
  }))
})

describe('built-in lists in All lists', () => {
  it('sit in their own group after the own lists, with the count and the note, or no count', async () => {
    mount()
    const block = await screen.findByRole('region', { name: 'Built-in (locked)' })
    const fno = within(block).getByRole('button', { name: /^F&O stocks/ })
    expect(fno).toHaveTextContent('Built-in, locked, 3 · As of 9 Nov 2026')
    const indices = within(block).getByRole('button', { name: /^Indices/ })
    expect(indices).toHaveTextContent('Built-in, locked')
    // Not known is never shown as 0.
    expect(indices.textContent).not.toMatch(/\d/)
    const yours = screen.getByRole('region', { name: 'Your lists' })
    expect(yours.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('a built-in list on the page', () => {
  it('shows its lock, the server’s lines once each and its rows in order, with no edit control', async () => {
    const user = userEvent.setup()
    mount()
    await openBuiltIn(user, /^F&O stocks/)

    const tab = await screen.findByRole('tab', { name: /F&O stocks, built-in, locked/ })
    expect(tab.querySelector('.lucide-lock')).not.toBeNull()
    for (const line of [LOCKED, SOURCE, MISSING]) {
      expect(screen.getAllByText(line)).toHaveLength(1)
    }
    const rows = await screen.findAllByRole('button', { name: /RELIANCE|TCS|INFY/ })
    expect(rows.map((el) => /RELIANCE|TCS|INFY/.exec(el.textContent ?? '')?.[0])).toEqual(SYMBOLS)

    // The controls are absent, not dead.
    expect(screen.queryByRole('button', { name: 'Add' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Reorder|Move up|Move down/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'List options' }))
    expect(await screen.findByRole('menuitem', { name: 'New list' })).toBeInTheDocument()
    for (const gone of [/Rename list/, /Reorder or remove/, /Delete list/, /New section/]) {
      expect(screen.queryByRole('menuitem', { name: gone })).toBeNull()
    }
    expect(screen.getByRole('menuitem', { name: 'View' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Alert on this list' })).toBeInTheDocument()
  })

  it('keeps sort, flags and notes working, and has no Remove in the symbol sheet', async () => {
    const user = userEvent.setup()
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    await screen.findAllByRole('button', { name: /RELIANCE/ })

    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Name' }))
    await waitFor(() => expect(b().count('pref')).toBe(1))
    expect(b().calls.find((call) => call.op === 'pref')?.body).toMatchObject({
      ref: 'preset:fno_stocks',
      sort: 'name',
    })

    await user.keyboard('{Escape}')
    await user.click((await screen.findAllByRole('button', { name: /TCS/ }))[0])
    expect(await screen.findByRole('radiogroup', { name: 'Flag' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Note/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Remove from/ })).toBeNull()
  })

  it('shows the server’s window line once, over a list longer than 250', async () => {
    const user = userEvent.setup()
    mount()
    await openBuiltIn(user, /^Big list/)
    await screen.findByText('Built-in list, locked. Big list: 300 symbols.')
    expect(screen.getAllByText(WINDOW)).toHaveLength(1)
    expect(screen.getAllByText(SOURCE)).toHaveLength(1)
    expect(document.querySelector('[data-preset-lines] button')).not.toBeNull()
  })
})

describe('Make a copy', () => {
  it('sends every key in the order shown (after a sort too), shows the words and opens the copy', async () => {
    const user = userEvent.setup()
    b().on('copy', () => {
      own.lists = [
        ...own.lists,
        {
          id: 2,
          name: 'F&O stocks (my copy)',
          position: 1,
          items: SYMBOLS.map((symbol, index) => ({
            id: 20 + index,
            symbol,
            exchange: 'NSE',
            position: index,
          })),
        },
      ]
      return {
        list: 2,
        added: 3,
        skipped: 0,
        version: '1',
        words: 'Copied the first 250 of 500, in the order shown. A list holds 250.',
      }
    })
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    await screen.findAllByRole('button', { name: /RELIANCE/ })
    // Sort by name, Z to A: the order shown is TCS, RELIANCE, INFY.
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'View' }))
    await user.click(await screen.findByRole('button', { name: 'Name' }))
    await user.click(await screen.findByRole('button', { name: 'Z to A' }))
    await user.keyboard('{Escape}')
    await waitFor(() => {
      const shown = screen.getAllByRole('button', { name: /RELIANCE|TCS|INFY/ })
      expect(shown.map((el) => /RELIANCE|TCS|INFY/.exec(el.textContent ?? '')?.[0])).toEqual([
        'TCS',
        'RELIANCE',
        'INFY',
      ])
    })

    await user.click(await screen.findByRole('button', { name: 'Make a copy' }))
    const name = await screen.findByRole('textbox', { name: 'List name' })
    expect(name).toHaveValue('F&O stocks (my copy)')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(b().count('copy')).toBe(1))
    expect(b().calls.find((call) => call.op === 'copy')?.body).toEqual({
      from: { ref: 'preset:fno_stocks', keys: ['NSE:TCS', 'NSE:RELIANCE', 'NSE:INFY'] },
      to: { name: 'F&O stocks (my copy)' },
    })
    await waitFor(() =>
      expect(toasts.success).toHaveBeenCalledWith(
        'Copied the first 250 of 500, in the order shown. A list holds 250.'
      )
    )
    // The new list is open: its tab is the selected one and the built-in lines are gone.
    expect(await screen.findByRole('tab', { name: /F&O stocks \(my copy\)/ })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.queryByText(LOCKED)).toBeNull()
  })

  it('shows the server’s refusal in the dialog and stays open', async () => {
    const user = userEvent.setup()
    b().on('copy', () => ({
      status: 'error',
      code: 'limit',
      message: 'You have 50 lists, the most there can be. Delete one to make a new list.',
    }))
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    await user.click(await screen.findByRole('button', { name: 'Make a copy' }))
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('You have 50 lists')
    expect(screen.getByRole('textbox', { name: 'List name' })).toBeInTheDocument()
  })

  it('takes no tap for the first half second after it opens', async () => {
    const user = userEvent.setup()
    b().on('copy', () => ({ list: 2, added: 3, skipped: 0, version: '1', words: null }))
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    const make = await screen.findByRole('button', { name: 'Make a copy' })
    OPEN_GUARD.ms = 500
    await user.click(make)
    const save = await screen.findByRole('button', { name: 'Save' })
    await user.click(save)
    expect(b().count('copy')).toBe(0)
    await new Promise((resolve) => setTimeout(resolve, 600))
    await user.click(save)
    await waitFor(() => expect(b().count('copy')).toBe(1))
  })
})

describe('a built-in list that cannot be shown', () => {
  it('says the server’s sentence in place of rows when the master is not loaded', async () => {
    const user = userEvent.setup()
    const said =
      'Today’s contract list is not loaded yet. It loads after the day’s login; open this list again then.'
    b().on('view', () => ({ status: 'error', code: 'failed', message: said }))
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    expect(await screen.findByRole('alert')).toHaveTextContent(said)
    expect(screen.queryByText('This list is empty')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Make a copy' })).toBeNull()
  })

  it('says the server’s sentence for an unknown built-in list, with no retry', async () => {
    const user = userEvent.setup()
    const said = 'That built-in list is not offered on this server.'
    b().on('view', () => ({ status: 'error', code: 'gone', message: said }))
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    expect(await screen.findByRole('alert')).toHaveTextContent(said)
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })
})

describe('Alert on this list', () => {
  it('opens with no count while the built-in list’s count is not known', async () => {
    const user = userEvent.setup()
    b().on('state', () =>
      state([{ slot: 1, ref: 'preset:indices', kind: 'preset', name: 'Indices', count: null }])
    )
    b().on('view', () => ({
      status: 'error',
      code: 'failed',
      message: 'Today’s contract list is not loaded yet.',
    }))
    mount('/watchlist')
    await user.click(await screen.findByRole('tab', { name: /Indices/ }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Alert on this list' }))
    const where = await screen.findByTestId('where')
    expect(where.textContent).toContain('ref=preset%3Aindices')
    expect(where.textContent).not.toContain('count')
  })

  it('passes the count the list was read with once it is shown', async () => {
    const user = userEvent.setup()
    mount()
    await openBuiltIn(user, /^F&O stocks/)
    await screen.findAllByRole('button', { name: /RELIANCE/ })
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Alert on this list' }))
    expect((await screen.findByTestId('where')).textContent).toContain('count=3')
  })
})

describe('with the switch off', () => {
  it('is today’s page: no strip, no All lists, and a copy address does nothing', async () => {
    b().on('state', () => ({ ...state([], false), enabled: false }))
    mount('/watchlist?sheet=copy')
    await screen.findByRole('tab', { name: /Main/ })
    expect(screen.queryByRole('button', { name: /All lists/ })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Make a copy' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Make a copy' })).toBeNull()
    expect(b().count('view')).toBe(0)
    expect(b().count('lists')).toBe(0)
  })
})
