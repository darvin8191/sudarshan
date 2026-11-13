/**
 * L11 on the page: when the bridge refuses `state` with the record sentence (the store is newer
 * than this app, or cannot be read), the owner reads that sentence; the page does not quietly turn
 * into the old one. The bridge is the v2 mock; the lists API is an in-memory stand-in.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const SENTENCE =
  'The watchlist store was written by a newer SUDARSHAN. Update the app, then open this page again.'

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  useAuthStore.setState({ apiKey: 'key' })
})

function open() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('a record refusal of state', () => {
  it('shows the server sentence, beside the lists the page still has', async () => {
    b().on('state', () => ({ status: 'error', code: 'record', message: SENTENCE }))
    open()
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument()
    expect(await screen.findByText('SBIN')).toBeInTheDocument()
  })

  it('says nothing when the features are simply switched off', async () => {
    b().on('state', () => ({ enabled: false }))
    open()
    expect(await screen.findByText('SBIN')).toBeInTheDocument()
    expect(screen.queryByText(SENTENCE)).toBeNull()
  })

  it('says nothing when the bridge could not be reached', async () => {
    b().on('state', () => ({ status: 'error', code: 'busy', message: 'The watchlists are busy.' }))
    open()
    expect(await screen.findByText('SBIN')).toBeInTheDocument()
    expect(screen.queryByText('The watchlists are busy.')).toBeNull()
  })
})
