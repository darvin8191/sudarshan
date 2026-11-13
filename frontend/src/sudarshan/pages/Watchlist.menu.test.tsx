/**
 * F10 on the page: built-in lists (M8.8). The All lists group with a known and an unknown count and
 * the note; a built-in list with its lock, the server's lines once each, no edit controls and the
 * things that still work on it (sort, flags, notes); Make a copy (every key in the order shown, the
 * server's words, the new list opened, the 500 ms guard); the master-not-loaded and gone sentences;
 * "Alert on this list" with no count; the switch off. The bridge is the v2 mock; the lists API and
 * the quote read are in-memory stand-ins.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

import { watchlistApi } from '@/api/watchlist'
import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import type { Row, View } from '@/sudarshan/watchlist/v2/types'
import Watchlist from './Watchlist'

/**
 * M8.6: the list menu acts only on the list on screen. A flag list offers Rename this flag and
 * neither Reorder or remove nor Delete list; a built-in list keeps F10's menu; an own list keeps
 * today's; Delete names the list it will delete and sends nothing if another list comes on screen.
 */
const b = () => bridge.current as NonNullable<typeof bridge.current>

const row = (symbol: string): Row => ({
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
  flag: 2,
  has_note: false,
  expired: false,
  metric: null,
})

const names = ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'].map((name, i) => ({
  mark: i + 1,
  name,
}))
const listEntry = (id: number, name: string) => ({
  ref: `list:${id}`,
  kind: 'list',
  name,
  count: 1,
  room: 249,
  locked: false,
  note: null,
})
const stateOf = (enabled: boolean) => ({
  enabled,
  versions: { lists: 'a:1', favourites: 1, flags: 7 },
  flag_names: names,
  favourites: [],
})

function mount(entry = '/watchlist') {
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

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'List options' }))
  await screen.findByRole('menuitem', { name: 'New list' })
}

async function openFromAllLists(
  user: ReturnType<typeof userEvent.setup>,
  region: string,
  name: RegExp
) {
  const block = await screen.findByRole('region', { name: region })
  await user.click(within(block).getByRole('button', { name }))
}

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  vi.mocked(watchlistApi.remove).mockClear()
  useAuthStore.setState({ apiKey: 'key' })
  own.lists = [
    {
      id: 1,
      name: 'Main',
      position: 0,
      items: [{ id: 11, symbol: 'SBIN', exchange: 'NSE', position: 0 }],
    },
    { id: 2, name: 'Swing', position: 1, items: [] },
  ]
  b().on('state', () => stateOf(true))
  b().on('lists', () => ({
    version: 'a:1',
    own: [listEntry(1, 'Main'), listEntry(2, 'Swing')],
    presets: [
      {
        ref: 'preset:indices',
        kind: 'preset',
        name: 'Indices',
        count: 2,
        room: null,
        locked: true,
        note: null,
      },
    ],
    ranked: [],
    flags: [
      {
        ref: 'flag:2',
        kind: 'flag',
        name: 'Breakout',
        count: 1,
        room: null,
        locked: true,
        note: null,
      },
    ],
    favourites: [],
  }))
  b().on('view', (call) => {
    const ref = (call.params as { ref: string }).ref
    return {
      ref,
      kind: ref.split(':')[0],
      name: ref.startsWith('flag:') ? 'Breakout' : ref.startsWith('preset:') ? 'Indices' : 'Main',
      version: '0',
      locked: !ref.startsWith('list:'),
      as_of: null,
      lines: [],
      total: 1,
      missing: [],
      sections: [],
      rows: ref.startsWith('list:') ? [] : [row('INFY')],
      follow: [],
      pref: { sort: 'list', descending: false, basis: 'close', held: false },
    } satisfies Partial<View>
  })
  b().on('flags', () => ({ version: 8 }))
})

describe('the list menu follows the list on screen', () => {
  it('an own list keeps today’s menu', async () => {
    const user = userEvent.setup()
    mount()
    await openMenu(user)
    expect(screen.queryByRole('menuitem', { name: 'Rename this flag' })).toBeNull()
    expect(screen.getByRole('menuitem', { name: 'Rename list' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Reorder or remove' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Delete list' })).toBeInTheDocument()
  })

  it('a flag list offers Rename this flag and nothing that edits or deletes a list', async () => {
    const user = userEvent.setup()
    mount('/watchlist?sheet=lists')
    await openFromAllLists(user, 'Flags', /^Breakout/)
    await screen.findByText(/INFY/)
    await openMenu(user)
    expect(screen.getByRole('menuitem', { name: 'Rename this flag' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Alert on this list' })).toBeInTheDocument()
    for (const gone of ['Rename list', 'Reorder or remove', 'Delete list', 'New section'])
      expect(screen.queryByRole('menuitem', { name: gone })).toBeNull()

    await user.click(screen.getByRole('menuitem', { name: 'Rename this flag' }))
    const name = await screen.findByRole('textbox', { name: 'Flag name' })
    expect(name).toHaveValue('Breakout')
    await user.clear(name)
    await user.type(name, 'Big move')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('flags')).toBe(1))
    const sent = b().calls.find((call) => call.op === 'flags')?.body as {
      names: Array<{ mark: number; name: string }>
      version: number
    }
    expect(sent.version).toBe(7)
    expect(sent.names).toEqual([
      { mark: 1, name: 'Watch' },
      { mark: 2, name: 'Big move' },
      { mark: 3, name: 'Results' },
      { mark: 4, name: 'Long term' },
      { mark: 5, name: 'Avoid' },
    ])
    expect(watchlistApi.remove).not.toHaveBeenCalled()
  })

  it('a built-in list keeps F10’s menu: no Rename, Reorder or remove, or Delete', async () => {
    const user = userEvent.setup()
    mount('/watchlist?sheet=lists')
    await openFromAllLists(user, 'Built-in (locked)', /^Indices/)
    await screen.findByText(/INFY/)
    await openMenu(user)
    expect(screen.getByRole('menuitem', { name: 'View' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Alert on this list' })).toBeInTheDocument()
    for (const gone of ['Rename list', 'Rename this flag', 'Reorder or remove', 'Delete list'])
      expect(screen.queryByRole('menuitem', { name: gone })).toBeNull()
  })

  it('a list kind the menu does not know offers nothing that edits', async () => {
    const { menuFor } = await import('@/sudarshan/watchlist/v2/menuRules')
    for (const kind of ['preset', 'ranked', 'something-later'])
      expect(menuFor(kind)).toEqual({ rename: null, edit: false, remove: false })
  })
})

describe('Delete list', () => {
  it('names the list and deletes that list’s id', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByRole('tab', { name: /Main/ })
    await user.click(screen.getByRole('tab', { name: /Swing/ }))
    await openMenu(user)
    await user.click(screen.getByRole('menuitem', { name: 'Delete list' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete “Swing”?' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(watchlistApi.remove).toHaveBeenCalledWith(2))
    expect(watchlistApi.remove).toHaveBeenCalledTimes(1)
  })

  it('closes with nothing sent when another list comes on screen while it is open', async () => {
    const user = userEvent.setup()
    mount()
    const swing = await screen.findByRole('tab', { name: /Swing/ })
    await openMenu(user)
    await user.click(screen.getByRole('menuitem', { name: 'Delete list' }))
    await screen.findByRole('dialog', { name: 'Delete “Main”?' })
    // The list on screen changes under the open confirmation (a tab tapped from another path).
    fireEvent.click(swing)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Delete/ })).toBeNull())
    expect(watchlistApi.remove).not.toHaveBeenCalled()
  })
})

describe('with the switch off', () => {
  it('is today’s page with today’s menu', async () => {
    b().on('state', () => ({ ...stateOf(false), enabled: false }))
    const user = userEvent.setup()
    mount()
    await screen.findByRole('tab', { name: /Main/ })
    await openMenu(user)
    for (const kept of ['Rename list', 'Reorder or remove', 'Delete list'])
      expect(screen.getByRole('menuitem', { name: kept })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Rename this flag' })).toBeNull()
    expect(b().count('view')).toBe(0)
  })
})
