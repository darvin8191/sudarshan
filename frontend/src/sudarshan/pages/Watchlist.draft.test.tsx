/**
 * GA6.4 on the page: moves made in edit mode and not saved survive leaving the page by the bottom
 * bar. Coming back the same day opens edit mode again with them; a list changed on another screen
 * meanwhile drops them with a sentence. The bridge is the v2 mock; the lists API an in-memory stand-in.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
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

type Item = { id: number; symbol: string; exchange: string; position: number }
const own = vi.hoisted(() => ({
  lists: [] as Array<{ id: number; name: string; position: number; items: Item[] }>,
}))
vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: { list: vi.fn(async () => own.lists) },
}))
const calendar = vi.hoisted(() => ({
  value: { isLoading: false, error: null, isMarketOpen: () => true, isAnyMarketOpen: () => true },
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
const said = vi.hoisted(() => ({ info: [] as string[] }))
vi.mock('@/utils/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: (text: string) => said.info.push(text),
  },
}))

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { readDraft } from '@/sudarshan/watchlist/v2/draftStore'
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const version = { now: '7' }

const item = (id: number, symbol: string, position: number): Item => ({
  id,
  symbol,
  exchange: 'NSE',
  position,
})

/** The bottom bar, as far as this page is concerned: it leaves for another page and comes back. */
function BottomBar() {
  const navigate = useNavigate()
  return (
    <nav>
      <button type="button" onClick={() => navigate('/dashboard')}>
        Go to Dashboard
      </button>
      <button type="button" onClick={() => navigate('/watchlist')}>
        Go to Watchlist
      </button>
    </nav>
  )
}

function mount() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <BottomBar />
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/dashboard" element={<h1>Dashboard</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const arranged = () => b().calls.filter((call) => call.op === 'arrange')

async function moveOne(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('tab', { name: /Main/ })
  await user.click(screen.getByRole('button', { name: 'List options' }))
  await user.click(await screen.findByRole('menuitem', { name: 'Reorder or remove' }))
  await user.click(await screen.findByRole('button', { name: 'Move SBIN down' }))
  await screen.findByText('The new order is not saved yet.')
}

async function leaveAndReturn(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Go to Dashboard', hidden: true }))
  await screen.findByRole('heading', { name: 'Dashboard' })
  await user.click(screen.getByRole('button', { name: 'Go to Watchlist', hidden: true }))
  await screen.findByRole('tab', { name: /Main/ })
}

beforeEach(() => {
  b().reset()
  said.info.length = 0
  version.now = '7'
  OPEN_GUARD.ms = 0
  localStorage.clear()
  useAuthStore.setState({ apiKey: 'key' })
  own.lists = [
    {
      id: 1,
      name: 'Main',
      position: 0,
      items: [item(11, 'SBIN', 0), item(12, 'INFY', 1), item(13, 'TCS', 2)],
    },
  ]
  b().on('state', () => ({
    enabled: true,
    versions: { lists: 'a:1', favourites: 1, flags: 1 },
    flag_names: [],
    favourites: [],
  }))
  b().on('view', () => ({
    ref: 'list:1',
    kind: 'list',
    name: 'Main',
    version: version.now,
    locked: false,
    as_of: null,
    lines: [],
    total: 3,
    missing: [],
    sections: [],
    rows: [],
    follow: [],
    pref: { sort: 'list', descending: false, basis: 'close', held: false },
  }))
  b().on('arrange', () => ({ version: '8', id: 1, words: null }))
})

describe('unsaved moves and the bottom bar (GA6.4)', () => {
  it('come back in edit mode, and Save order sends them', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await leaveAndReturn(user)
    expect(await screen.findByText('The new order is not saved yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move SBIN up' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(arranged()).toHaveLength(1))
    expect(arranged()[0].body).toMatchObject({
      id: 1,
      version: '7',
      items: [{ symbol: 'INFY' }, { symbol: 'SBIN' }, { symbol: 'TCS' }],
    })
    await waitFor(() => expect(readDraft()).toBeNull())
    expect(said.info).toEqual([])
  })

  it('are dropped with a sentence when the list changed on another screen meanwhile', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: 'Go to Dashboard', hidden: true }))
    await screen.findByRole('heading', { name: 'Dashboard' })
    version.now = '9' // the other screen's write
    await user.click(screen.getByRole('button', { name: 'Go to Watchlist', hidden: true }))
    await waitFor(() =>
      expect(said.info).toEqual([
        'Your unsaved moves in Main were dropped: the list was changed on another screen.',
      ])
    )
    expect(await screen.findByRole('button', { name: 'Add' })).toBeInTheDocument()
    expect(screen.queryByText('The new order is not saved yet.')).toBeNull()
    expect(readDraft()).toBeNull()
    expect(arranged()).toHaveLength(0)
  })
})
