/**
 * What a tick costs the Positions page (M6.9), measured. Ticks arrive one per
 * WebSocket message, so each is delivered here in its own `act`, as the browser
 * delivers each message in its own task.
 *
 * - The page: counted by the one hook only the page calls on every render.
 * - Each card: counted by the name it reads (`describe`) on every render.
 * - Commits: React's own profiler, around the whole page.
 *
 * The wide table is upstream's `useLivePrice`, which keeps every tick in the
 * page's state; it is measured too, as the comparison.
 */

import { QueryClientProvider } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import { Profiler, useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Position } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({ getPositions: vi.fn(), getOrders: vi.fn(), getHoldings: vi.fn() }))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => api.getPositions(...args),
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getHoldings: (...args: unknown[]) => api.getHoldings(...args),
    getMultiQuotes: vi.fn().mockResolvedValue({ status: 'success', results: [] }),
    closePosition: vi.fn(),
    closeAllPositions: vi.fn(),
  },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick({ appMode: 'live' }),
}))
vi.mock('@/components/trading/GttTab', () => ({ default: () => null }))
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
    isLoading: false,
    error: null,
  }),
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

const count = vi.hoisted(() => ({ page: 0, board: 0, cards: new Map<string, number>() }))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => {
    count.page += 1
    return { isCrypto: false }
  },
}))
// The board: counted by the one hook only a book's board calls, once a render.
vi.mock('@/sudarshan/books/cards/useBookOverlay', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/sudarshan/books/cards/useBookOverlay')>()
  return {
    ...real,
    useBookOverlay: (...args: Parameters<typeof real.useBookOverlay>) => {
      count.board += 1
      return real.useBookOverlay(...args)
    },
  }
})
vi.mock('@/sudarshan/watchlist/rows', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/sudarshan/watchlist/rows')>()
  return {
    ...real,
    describe: (symbol: string, exchange: string) => {
      count.cards.set(symbol, (count.cards.get(symbol) ?? 0) + 1)
      return real.describe(symbol, exchange)
    },
  }
})

import { createQueryClient } from '@/app/providers'
import Holdings from '@/pages/Holdings'
import OrderBook from '@/pages/OrderBook'
import Positions from '@/pages/Positions'
import { frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { fakeScreen } from '@/test/screenWidth'
import type { Order } from '@/types/trading'
import { PositionCards } from './PositionCards'

// Fifty renders of the wide table take a few seconds on a busy machine; nothing here is timed.
vi.setConfig({ testTimeout: 30_000 })

/** Twenty open NIFTY calls, 25,000 to 25,950. */
const BOOK: Position[] = Array.from({ length: 20 }, (_, index) => ({
  symbol: `NIFTY27OCT26${25000 + index * 50}CE`,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 100,
  ltp: 110,
  pnl: 650,
  pnlpercent: 10,
}))

/** When the book above was asked for, and a market that is open. */
const BOOK_AT = Date.UTC(2026, 9, 20, 5, 12, 0)
const OPEN = () => true

/** Five of them tick, ten times each, one tick per message. */
const TICKING = BOOK.slice(0, 5).map((position) => position.symbol)

let commits = 0

async function renderAt(width: 'phone' | 'wide', page = <Positions />) {
  fakeScreen(width)
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={['/positions']}>
        <Profiler
          id="book"
          onRender={() => {
            commits += 1
          }}
        >
          {page}
        </Profiler>
      </MemoryRouter>
    </QueryClientProvider>
  )
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
  act(() => frames.flush())
}

function resetCounts() {
  commits = 0
  count.page = 0
  count.board = 0
  count.cards.clear()
}

/** Fifty ticks, each in its own message. */
function fiftyTicks(start: number) {
  for (let round = 0; round < 10; round += 1) {
    for (const symbol of TICKING) {
      act(() => feed.push(symbol, 'NFO', { ltp: start + round + TICKING.indexOf(symbol) / 10 }))
    }
  }
}

beforeEach(() => {
  // A clock that moves only when told: no second-hand re-render inside a measurement.
  vi.useFakeTimers({
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
  })
  frames.install()
  feed.reset()
  resetCounts()
  api.getPositions.mockReset().mockResolvedValue({ status: 'success', data: BOOK })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('What a tick costs the Positions page', () => {
  it('on a phone: no page render, one commit per frame, only the cards that moved', async () => {
    await renderAt('phone')
    expect(count.cards.size).toBe(20)
    resetCounts()

    fiftyTicks(111)
    // Nothing is drawn until the frame: fifty ticks have rendered nothing yet.
    expect(commits).toBe(0)
    act(() => frames.flush())

    expect(count.page).toBe(0)
    expect(commits).toBe(1)
    expect([...count.cards.keys()].sort()).toEqual([...TICKING].sort())
    expect([...count.cards.values()]).toEqual([1, 1, 1, 1, 1])

    // One tick a frame: one commit each, and only its own card redrawn.
    resetCounts()
    for (let i = 0; i < 10; i += 1) {
      act(() => feed.push(TICKING[0], 'NFO', { ltp: 130 + i }))
      act(() => frames.flush())
    }
    expect(count.page).toBe(0)
    expect(commits).toBe(10)
    expect(Object.fromEntries(count.cards)).toEqual({ [TICKING[0]]: 10 })

    // The second hand (the age on the status lines) redraws the page's own
    // line and the summary once a second, but not a card whose figures stand.
    resetCounts()
    act(() => {
      vi.advanceTimersByTime(1_000)
    })
    expect(count.page).toBe(1)
    expect(count.cards.size).toBe(0)
  })

  it('on a phone: a render of the page alone does not redraw the board', async () => {
    // Upstream's page renders for reasons of its own and builds its handlers
    // afresh each time; the board takes them through useLatest and is memoised,
    // so only what it shows redraws it. A page that renders by itself:
    const bump = { current: () => {} }
    function Page() {
      const [renders, setRenders] = useState(0)
      bump.current = () => setRenders((n) => n + 1)
      return (
        <PositionCards
          positions={BOOK}
          bookAt={BOOK_AT}
          loading={false}
          error={null}
          refreshing={false}
          apiKey="test-api-key"
          marketOpen={OPEN}
          onRefresh={() => renders}
          onExit={() => renders}
          onExitAll={() => renders}
        />
      )
    }
    await renderAt('phone', <Page />)
    resetCounts()
    for (let i = 0; i < 3; i += 1) act(() => bump.current())
    expect(count.board).toBe(0)
    expect(count.cards.size).toBe(0)
  })

  it('on a phone’s order book: a tick redraws only the working order of its instrument', async () => {
    // Ten orders, each on its own instrument: two still working.
    const orders = BOOK.slice(0, 10).map(
      (position, index): Order => ({
        symbol: position.symbol,
        exchange: 'NFO',
        action: 'BUY',
        quantity: 65,
        price: 100,
        trigger_price: 0,
        pricetype: 'LIMIT',
        product: 'MIS',
        orderid: String(1000 + index),
        order_status: index < 2 ? 'open' : 'complete',
        timestamp: `2026-10-20 10:${String(10 + index).padStart(2, '0')}:00`,
      })
    )
    api.getOrders.mockResolvedValue({ status: 'success', data: { orders, statistics: {} } })
    await renderAt('phone', <OrderBook />)
    expect(count.cards.size).toBe(10)
    resetCounts()

    for (let i = 0; i < 10; i += 1) {
      act(() => feed.push(orders[0].symbol, 'NFO', { ltp: 120 + i }))
    }
    act(() => frames.flush())
    expect(commits).toBe(1)
    expect(Object.fromEntries(count.cards)).toEqual({ [orders[0].symbol]: 1 })
  })

  it('on a phone’s holdings: a frame redraws only the holdings that moved', async () => {
    // Twenty delivery holdings, each its own instrument; five of them tick.
    const holdings = Array.from({ length: 20 }, (_, index) => ({
      symbol: `STOCK${index}`,
      exchange: 'NSE',
      product: 'CNC',
      quantity: 10,
      average_price: 100,
      ltp: 110,
      pnl: 100,
      pnlpercent: 10,
    }))
    api.getHoldings.mockResolvedValue({
      status: 'success',
      data: {
        holdings,
        statistics: {
          totalholdingvalue: 22000,
          totalinvvalue: 20000,
          totalprofitandloss: 2000,
          totalpnlpercentage: 10,
        },
      },
    })
    await renderAt('phone', <Holdings />)
    expect(count.cards.size).toBe(20)
    resetCounts()

    const moving = holdings.slice(0, 5).map((holding) => holding.symbol)
    for (let round = 0; round < 10; round += 1) {
      for (const symbol of moving) act(() => feed.push(symbol, 'NSE', { ltp: 111 + round }))
    }
    act(() => frames.flush())
    expect(commits).toBe(1)
    expect([...count.cards.keys()].sort()).toEqual([...moving].sort())
    expect([...count.cards.values()]).toEqual([1, 1, 1, 1, 1])
  })

  it('on a wide screen (upstream’s table, for comparison): the whole page, once per tick', async () => {
    await renderAt('wide')
    resetCounts()

    fiftyTicks(111)
    expect(count.page).toBe(50)
    expect(commits).toBe(50)
  })
})
