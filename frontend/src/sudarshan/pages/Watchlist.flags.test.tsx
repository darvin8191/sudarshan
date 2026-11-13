/**
 * F8 on the page: a flag list opened from All lists shows its rows newest flag first, read-only
 * (no Add, no edit, no Remove), with its words; an expired contract reads Expired. The bridge is
 * the v2 mock; the lists API and the quote read are in-memory stand-ins.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
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

vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: {
    list: vi.fn(async () => [
      {
        id: 1,
        name: 'Main',
        position: 0,
        items: [{ id: 11, symbol: 'SBIN', exchange: 'NSE', position: 0 }],
      },
    ]),
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
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import type { Row, View } from '@/sudarshan/watchlist/v2/types'
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const flagRow = (symbol: string, flag: number, note: boolean, expired = false): Row => ({
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
  has_note: note,
  expired,
  metric: null,
})
// The server's order: newest flag first.
const NEWEST_FIRST = [
  flagRow('INFY', 2, true),
  flagRow('TCS', 2, false),
  flagRow('GONE25FUT', 2, false, true),
]
const names = ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'].map((name, i) => ({
  mark: i + 1,
  name,
}))

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  useAuthStore.setState({ apiKey: 'key' })
  b().on('state', () => ({
    enabled: true,
    versions: { lists: 'a:1', favourites: 1, flags: 1 },
    flag_names: names,
    favourites: [],
  }))
  b().on('lists', () => ({
    version: 'a:1',
    own: [
      { ref: 'list:1', kind: 'list', name: 'Main', count: 1, room: 249, locked: false, note: null },
    ],
    presets: [],
    ranked: [],
    flags: [
      {
        ref: 'flag:2',
        kind: 'flag',
        name: 'Breakout',
        count: 3,
        room: null,
        locked: true,
        note: null,
      },
    ],
    favourites: [],
  }))
  b().on('symbol', () => ({
    key: 'NSE:INFY',
    flag: 2,
    flag_name: 'Breakout',
    note: null,
    note_at: null,
    expired: false,
    version: 1,
    lists: [],
  }))
  b().on('view', (call) => {
    const ref = (call.params as { ref: string }).ref
    return {
      ref,
      kind: ref.startsWith('flag:') ? 'flag' : 'list',
      name: 'x',
      version: '0',
      locked: ref.startsWith('flag:'),
      as_of: null,
      lines: [],
      missing: [],
      sections: [],
      rows: ref.startsWith('flag:') ? NEWEST_FIRST : [],
      follow: [],
      pref: { sort: 'list', descending: false, basis: 'close', held: false },
    } satisfies Partial<View>
  })
})

describe('a flag list on the page', () => {
  it('opens from All lists with its rows newest flag first, read-only, Expired marked', async () => {
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/watchlist?sheet=lists']}>
          <Routes>
            <Route path="/watchlist" element={<Watchlist />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
    const block = await screen.findByRole('region', { name: 'Flags' })
    await user.click(within(block).getByRole('button', { name: /^Breakout/ }))

    const list = await screen.findAllByRole('button', { name: /INFY|TCS|GONE25FUT/ })
    expect(list.map((el) => /INFY|TCS|GONE25FUT/.exec(el.textContent ?? '')?.[0])).toEqual([
      'INFY',
      'TCS',
      'GONE25FUT',
    ])
    expect(
      screen.getByText('Flagged across your lists. Flag or unflag a symbol from its sheet.')
    ).toBeInTheDocument()
    expect(list[0]).toHaveTextContent('Flag: Breakout. Has a note.')
    expect(list[2].querySelector('[data-expired="true"]')).toHaveTextContent('Expired')
    // Read-only: no Add, no edit, and the sheet has no Remove.
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    await user.click(list[0])
    await screen.findByRole('radiogroup', { name: 'Flag' })
    expect(screen.queryByRole('button', { name: /^Remove from/ })).toBeNull()
    await waitFor(() => expect(b().count('view')).toBeGreaterThan(0))
  })
})
