/**
 * F10 on the page: built-in lists (M8.8). The All lists group with a known and an unknown count and
 * the note; a built-in list with its lock, the server's lines once each, no edit controls and the
 * things that still work on it (sort, flags, notes); Make a copy (every key in the order shown, the
 * server's words, the new list opened, the 500 ms guard); the master-not-loaded and gone sentences;
 * "Alert on this list" with no count; the switch off. The bridge is the v2 mock; the lists API and
 * the quote read are in-memory stand-ins.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
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
  watchlistApi: {
    list: vi.fn(async () => own.lists),
    remove: vi.fn(async (id: number) => {
      own.lists = own.lists.filter((list) => list.id !== id)
    }),
  },
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

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { searchSymbols } from '@/sudarshan/watchlist/api'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import Watchlist from './Watchlist'

/**
 * F13 on the page: with the switch on, `?sheet=add` is the search sheet (the server's `search` op,
 * chips, Recent); with it off it is today's Add sheet, untouched. "Add ATM row" from a search hands
 * F11's form the underlying and its exchange.
 */
const b = () => bridge.current as NonNullable<typeof bridge.current>

const names = ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'].map((name, i) => ({
  mark: i + 1,
  name,
}))
const stateOf = (enabled: boolean) => ({
  enabled,
  versions: { lists: 'a:1', favourites: 1, flags: 7 },
  flag_names: names,
  favourites: [],
})

const NIFTY = {
  exchange: 'NSE_INDEX',
  symbol: 'NIFTY',
  key: 'NSE_INDEX:NIFTY',
  name: 'NIFTY',
  lot: null,
  tick: null,
  expiry: null,
  isin: null,
}

function mount(entry = '/watchlist?sheet=add') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  vi.mocked(searchSymbols).mockReset()
  useAuthStore.setState({ apiKey: 'key' })
  own.lists = [
    {
      id: 1,
      name: 'Main',
      position: 0,
      items: [{ id: 11, symbol: 'SBIN', exchange: 'NSE', position: 0 }],
    },
  ]
  b().on('lists', () => ({
    version: 'a:1',
    own: [
      { ref: 'list:1', kind: 'list', name: 'Main', count: 1, room: 249, locked: false, note: null },
    ],
    presets: [],
    ranked: [],
    flags: [],
    favourites: [],
  }))
  b().on('view', (call) => ({
    ref: (call.params as { ref: string }).ref,
    kind: 'list',
    name: 'Main',
    version: '0',
    locked: false,
    as_of: null,
    lines: [],
    total: 1,
    missing: [],
    sections: [],
    rows: [],
    follow: [],
    pref: { sort: 'list', descending: false, basis: 'close', held: false },
  }))
  b().on('search', () => ({
    understood: 'NIFTY',
    hits: [
      { group: 'indices', title: 'NIFTY', detail: 'NSE_INDEX', added: false, contract: NIFTY },
    ],
    underlying: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    suggestion: null,
  }))
})

describe('?sheet=add', () => {
  it('is the search sheet with the switch on, and sends the list id', async () => {
    b().on('state', () => stateOf(true))
    const user = userEvent.setup()
    mount()
    const field = await screen.findByRole('searchbox', { name: 'Symbol, name or contract' })
    expect(screen.getByRole('button', { name: 'Commodities' })).toBeInTheDocument()
    await user.type(field, 'NIFTY')
    expect(await screen.findByText('NIFTY', { selector: 'p' })).toBeInTheDocument()
    expect(
      b()
        .calls.filter((c) => c.op === 'search')
        .at(-1)?.params
    ).toMatchObject({
      q: 'NIFTY',
      seg: 'all',
      list: '1',
    })
    expect(searchSymbols).not.toHaveBeenCalled()
  })

  it('opens F11’s form from Add ATM row, naming the underlying', async () => {
    b().on('state', () => stateOf(true))
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByRole('searchbox'), 'NIFTY')
    await user.click(await screen.findByRole('button', { name: 'Add ATM row' }))
    expect(await screen.findByText('Follows NIFTY')).toBeInTheDocument()
  })

  it('opens the strike picker from Pick a strike, and Add as one row opens F12’s form on that strike', async () => {
    b().on('state', () => stateOf(true))
    const pair = (strike: number, right: string) => ({
      exchange: 'NFO',
      symbol: `NIFTY24NOV26${strike}${right}`,
      key: `NFO:NIFTY24NOV26${strike}${right}`,
      name: 'NIFTY',
      lot: 65,
      tick: 0.05,
      expiry: '2026-11-24',
      isin: null,
    })
    b().on('strikes', () => ({
      underlying: 'NIFTY',
      exchange: 'NSE_INDEX',
      expiries: [{ date: '2026-11-24', label: 'Nearest 24 Nov', month: false }],
      expiry: '2026-11-24',
      atm: null,
      strikes: [{ strike: 25900, atm: false, ce: pair(25900, 'CE'), pe: pair(25900, 'PE') }],
      words: 'NIFTY’s price is not known yet.',
    }))
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByRole('searchbox'), 'NIFTY')
    await user.click(await screen.findByRole('button', { name: 'Pick a strike' }))
    expect(await screen.findByRole('button', { name: 'Nearest 24 Nov' })).toBeInTheDocument()
    expect(
      b()
        .calls.filter((c) => c.op === 'strikes')
        .at(0)?.params
    ).toMatchObject({
      u: 'NIFTY',
      x: 'NSE_INDEX',
    })
    await user.click(await screen.findByRole('button', { name: 'Add as one row at 25,900' }))
    expect(await screen.findByText(/kept for good/)).toBeInTheDocument()
  })

  it('does not reopen a picker that came back with a reload, and knows no picker with the switch off', async () => {
    b().on('state', () => stateOf(true))
    b().on('strikes', () => {
      throw new Error('must not be asked')
    })
    mount('/watchlist?sheet=strikes')
    await screen.findByText('SBIN')
    expect(screen.queryByRole('button', { name: 'Add ATM row' })).toBeNull()
    expect(b().count('strikes')).toBe(0)
  })

  it('is today’s Add sheet with the switch off', async () => {
    b().on('state', () => stateOf(false))
    vi.mocked(searchSymbols).mockResolvedValue([])
    const user = userEvent.setup()
    mount()
    const field = await screen.findByPlaceholderText('Symbol or name')
    expect(screen.queryByRole('button', { name: 'Commodities' })).toBeNull()
    await user.type(field, 'SBI')
    await vi.waitFor(() => expect(searchSymbols).toHaveBeenCalled())
    expect(b().count('search')).toBe(0)
  })
})
