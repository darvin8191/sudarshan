// SUDARSHAN P6 D4: the viewer's Dashboard, Positions and Holdings screens, through the real
// components with a fake service. Synthetic rows only.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketDataProvider } from '@/contexts/MarketDataContext'
import { PRICE_SCALE, QTY_SCALE } from '@/sudarshan/portfolio/types'
import { getFamilyManager, resetFamilyManagerForTests } from './familyManager'
import FamilyDashboard from './pages/FamilyDashboard'
import FamilyHoldings from './pages/FamilyHoldings'
import FamilyPositions from './pages/FamilyPositions'
import { bookOf, FakeSocket, ok, routeFetch } from './testing/fixtures'

const px = (rupees: number) => Math.round(rupees * PRICE_SCALE)
const qty = (units: number) => Math.round(units * QTY_SCALE)

const FULL_POSITION = {
  exchange: 'NSE',
  symbol: 'SYNTH',
  product: 'MIS',
  qty_u: qty(10),
  avg_price_u: px(100),
  last_price_u: px(105),
  pnl_paise: 5_000,
  multiplier: 1,
}
const MASKED_POSITION = {
  exchange: 'NSE',
  symbol: 'MASKD',
  product: 'MIS',
  side: 'long',
  last_price_u: px(205),
  close_price_u: px(200),
}
const FULL_HOLDING = {
  exchange: 'NSE',
  symbol: 'HELDX',
  qty_u: qty(4),
  avg_price_u: px(50),
  last_price_u: px(55),
  close_price_u: px(54),
  pnl_paise: 2_000,
}
const MASKED_HOLDING = {
  exchange: 'NSE',
  symbol: 'HIDDX',
  last_price_u: px(80),
  close_price_u: px(79),
}

function mount(path: string, element: React.ReactNode, route = '/family/positions') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MarketDataProvider manager={getFamilyManager()}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={route} element={element} />
          </Routes>
        </MemoryRouter>
      </MarketDataProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.unstubAllGlobals()
})

const FORBIDDEN = /^(exit|exit all|add|buy|buy more|sell|place|cancel|modify|square)/i

describe('Positions', () => {
  it('shows a masked row as Hidden, never as 0, and keeps its price', async () => {
    routeFetch({
      '/family/api/books/positions': ok(
        bookOf('positions', [MASKED_POSITION], { hidden: ['pnl', 'qty'] })
      ),
    })
    mount('/family/positions', <FamilyPositions />)
    const total = await screen.findByTestId('positions-total')
    expect(total.textContent).toContain('Hidden')
    const card = await screen.findByRole('button', { name: /MASKD/ })
    expect(card.textContent).toContain('Hidden')
    expect(card.textContent).toContain('205')
    expect(card.textContent).not.toMatch(/(^|[^\d.,])0(\.00)?($|[^\d])/)
    expect(card.textContent).toContain('LONG')
  })

  it('shows figures for a row the mask leaves', async () => {
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])) })
    mount('/family/positions', <FamilyPositions />)
    const total = await screen.findByTestId('positions-total')
    expect(total.textContent).toContain('50.00')
    expect(total.querySelector('[data-hidden]')).toBeNull()
  })

  it('has no action by accessible name, and no link out to an owner page', async () => {
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])) })
    mount('/family/positions', <FamilyPositions />)
    await screen.findByRole('button', { name: /SYNTH/ })
    for (const button of screen.getAllByRole('button')) {
      expect(button.getAttribute('aria-label') ?? button.textContent ?? '').not.toMatch(FORBIDDEN)
    }
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    // and the sheet it opens has none either
    await userEvent.click(screen.getByRole('button', { name: /SYNTH/ }))
    const sheet = await screen.findByRole('dialog')
    for (const button of within(sheet).getAllByRole('button')) {
      expect(button.getAttribute('aria-label') ?? button.textContent ?? '').not.toMatch(FORBIDDEN)
    }
    expect(within(sheet).queryAllByRole('link')).toHaveLength(0)
  })

  it('ignores ?act=exit in the address: no sheet, nothing sent', async () => {
    const router = routeFetch({
      '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])),
    })
    mount('/family/positions?act=exit&row=NSE:SYNTH:MIS', <FamilyPositions />)
    await screen.findByRole('button', { name: /SYNTH/ })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(router.calls.every((call) => call.method === 'GET')).toBe(true)
    expect(router.urls().every((url) => url.startsWith('/family/api/'))).toBe(true)
  })

  it('reads nothing from an owner endpoint', async () => {
    const router = routeFetch({
      '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])),
    })
    mount('/family/positions', <FamilyPositions />)
    await screen.findByRole('button', { name: /SYNTH/ })
    expect(router.urls().filter((url) => !url.startsWith('/family/api/'))).toEqual([])
  })

  it('says so, in the service words, when the read is not fresh', async () => {
    routeFetch({
      '/family/api/books/positions': ok(
        bookOf('positions', [FULL_POSITION], {
          state: 'unreachable',
          words: 'The account cannot be read right now; prices still move.',
        })
      ),
    })
    mount('/family/positions', <FamilyPositions />)
    expect(await screen.findByText(/cannot be read right now/)).toBeTruthy()
  })

  it('never says Live for a feed that is not delivering', async () => {
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])) })
    mount('/family/positions', <FamilyPositions />)
    await screen.findByRole('button', { name: /SYNTH/ })
    expect(screen.queryByText(/^Live$/)).toBeNull()
  })

  it('refetches the part when the service says it read it again', async () => {
    const router = routeFetch({
      '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])),
    })
    mount('/family/positions', <FamilyPositions />)
    await screen.findByRole('button', { name: /SYNTH/ })
    const before = router.urls().filter((url) => url.includes('/books/positions')).length
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'book', part: 'positions', read_at: Date.now() })
    })
    await waitFor(() =>
      expect(router.urls().filter((url) => url.includes('/books/positions')).length).toBe(
        before + 1
      )
    )
  })

  it('labels a stopped feed instead of showing old prices as current', async () => {
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])) })
    mount('/family/positions', <FamilyPositions />)
    await screen.findByRole('button', { name: /SYNTH/ })
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'feed_state', state: 'closed', since: Date.now() })
    })
    // the status line says it, and (review L4) so does the read stamp
    await waitFor(() =>
      expect(screen.getAllByText(/market closed|not real-time|closed/i).length).toBeGreaterThan(0)
    )
    expect(screen.queryByText(/^Live$/)).toBeNull()
  })

  it('past the symbol cap a row says it is not real-time', async () => {
    const rows = Array.from({ length: 151 }, (_, i) => ({
      ...FULL_POSITION,
      symbol: `SYN${String(i).padStart(3, '0')}`,
    }))
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', rows)) })
    const { container } = mount('/family/positions', <FamilyPositions />)
    await waitFor(() => expect(container.querySelectorAll('[data-position]').length).toBe(151), {
      timeout: 10_000,
    })
    const rowOf = (symbol: string) =>
      container.querySelector(`[data-position="NSE:${symbol}:MIS"]`)?.textContent ?? ''
    expect(rowOf('SYN150')).toContain('Not real-time')
    expect(rowOf('SYN149')).not.toContain('Not real-time')
    expect(rowOf('SYN000')).not.toContain('Not real-time')
  }, 20_000)
})

describe('Holdings', () => {
  it('shows a masked holding as Hidden and never as 0', async () => {
    routeFetch({
      '/family/api/books/holdings': ok(
        bookOf('holdings', [MASKED_HOLDING], { hidden: ['pnl', 'qty'] })
      ),
    })
    mount('/family/holdings', <FamilyHoldings />, '/family/holdings')
    const card = await screen.findByRole('button', { name: /HIDDX/ })
    expect(card.textContent).toContain('Hidden')
    expect(card.textContent).toContain('80')
    expect(screen.getByTestId('holdings-value').textContent).toContain('Hidden')
  })

  it('with quantities shown and P&L hidden, shows no value, invested or day change (H1)', async () => {
    // As an older server would leave it: the average is still in the row.
    const costly = { ...FULL_HOLDING, symbol: 'COSTX', pnl_paise: undefined }
    routeFetch({
      '/family/api/books/holdings': ok(bookOf('holdings', [costly], { hidden: ['pnl'] })),
    })
    mount('/family/holdings', <FamilyHoldings />, '/family/holdings')
    const card = await screen.findByRole('button', { name: /COSTX/ })
    expect(card.textContent).toContain('Qty 4')
    expect(card.textContent).toMatch(/Avg\s*Hidden/)
    expect(card.textContent).toMatch(/Value\s*Hidden/)
    expect(screen.getByTestId('holdings-value').textContent).toContain('Hidden')
    const header = screen.getByTestId('holdings-value').closest('section') as HTMLElement
    for (const label of ['Invested', 'Overall P&L', "Day's change"]) {
      const term = within(header).getByText(label)
      expect(term.nextElementSibling?.textContent).toContain('Hidden')
    }
    // 4 x 55 = 220 now, 4 x 50 = 200 invested, 4 x (55 - 54) = 4 today: none of them anywhere
    expect(document.body.textContent).not.toMatch(/220\.00|200\.00|₹4\.00/)
    await userEvent.click(card)
    const sheet = await screen.findByRole('dialog')
    for (const label of ['Average price', 'Invested', 'Current value', 'Day’s change']) {
      const term = within(sheet).getByText(label)
      expect(term.parentElement?.textContent).toContain('Hidden')
    }
  })

  it('shows value, invested and P&L where the mask leaves them', async () => {
    routeFetch({ '/family/api/books/holdings': ok(bookOf('holdings', [FULL_HOLDING])) })
    mount('/family/holdings', <FamilyHoldings />, '/family/holdings')
    await screen.findByRole('button', { name: /HELDX/ })
    expect(screen.getByTestId('holdings-value').textContent).toContain('220.00')
  })

  it('has no Buy more and no Sell, in the list or the sheet', async () => {
    routeFetch({ '/family/api/books/holdings': ok(bookOf('holdings', [FULL_HOLDING])) })
    mount('/family/holdings?act=buy&row=NSE:HELDX', <FamilyHoldings />, '/family/holdings')
    await screen.findByRole('button', { name: /HELDX/ })
    expect(screen.queryByRole('dialog')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /HELDX/ }))
    const sheet = await screen.findByRole('dialog')
    for (const button of within(sheet).getAllByRole('button')) {
      expect(button.getAttribute('aria-label') ?? button.textContent ?? '').not.toMatch(FORBIDDEN)
    }
    expect(within(sheet).queryAllByRole('link')).toHaveLength(0)
  })
})

describe('Dashboard', () => {
  const market = {
    feed: 'closed',
    since: null,
    indices: [
      {
        symbol: 'SYNIDX',
        exchange: 'NSE_INDEX',
        name: 'Synthetic Index',
        ltp: 1000,
        change: 1,
        change_percent: 0.1,
        close: 999,
        at: Date.now() - 60_000,
      },
    ],
  }

  it('shows the market strip and a hidden total for a masked member', async () => {
    routeFetch({
      '/family/api/market': ok(market),
      '/family/api/books/positions': ok(
        bookOf('positions', [MASKED_POSITION], { hidden: ['pnl', 'qty'] })
      ),
    })
    mount('/family', <FamilyDashboard />, '/family')
    expect(await screen.findByText('Synthetic Index')).toBeTruthy()
    const total = await screen.findByTestId('glance-total')
    expect(total.textContent).toContain('Hidden')
    expect(screen.queryByText(/^Live$/)).toBeNull()
  })

  it('shows the total for a member who may see it', async () => {
    routeFetch({
      '/family/api/market': ok(market),
      '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])),
    })
    mount('/family', <FamilyDashboard />, '/family')
    const total = await screen.findByTestId('glance-total')
    await waitFor(() => expect(total.textContent).toContain('50.00'))
  })

  // Review M3 (v0.12.0): the glance was worked out from the read alone and never moved between
  // reads, under a Real-time line. Fails on the old screen: it never asked for SYNTH's ticks.
  it('moves the glance total with a tick newer than the read', async () => {
    routeFetch({
      '/family/api/market': ok({ ...market, feed: 'realtime' }),
      '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])),
    })
    mount('/family', <FamilyDashboard />, '/family')
    const total = await screen.findByTestId('glance-total')
    await waitFor(() => expect(total.textContent).toContain('50.00'))
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'auth', status: 'success' })
    })
    // the screen follows the open position, not the indices alone
    await waitFor(() =>
      expect(FakeSocket.last().sent.some((sent) => sent.includes('"SYNTH"'))).toBe(true)
    )
    act(() => {
      FakeSocket.last().say({
        type: 'market_data',
        symbol: 'SYNTH',
        exchange: 'NSE',
        mode: 1,
        data: { ltp: 107, close: 100, ltt: Date.now(), timestamp: Date.now() },
      })
    })
    // 10 held at 100, read at 105 (P&L 50.00); a tick at 107 makes it 70.00
    await waitFor(() => expect(total.textContent).toContain('70.00'))
    expect(total.textContent).not.toContain('50.00')
  })

  it('does not follow a closed position, whose P&L is booked', async () => {
    routeFetch({
      '/family/api/market': ok(market),
      '/family/api/books/positions': ok(
        bookOf('positions', [{ ...FULL_POSITION, symbol: 'SHUTX', qty_u: 0, pnl_paise: 1_200 }])
      ),
    })
    mount('/family', <FamilyDashboard />, '/family')
    const total = await screen.findByTestId('glance-total')
    await waitFor(() => expect(total.textContent).toContain('12.00'))
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'auth', status: 'success' })
    })
    await waitFor(() =>
      expect(FakeSocket.last().sent.some((sent) => sent.includes('"SYNIDX"'))).toBe(true)
    )
    expect(FakeSocket.last().sent.some((sent) => sent.includes('"SHUTX"'))).toBe(false)
  })
})

// Review M4 (v0.12.0): "not shared", "portfolio off" and "cannot be read" came with no rows, and
// the screens said "No open positions", "No holdings" and "₹0.00": a zero that is not a zero. Each
// case fails on the old screens.
describe('a part that was not read is not an empty account', () => {
  const market = { feed: 'closed', since: null, indices: [] }
  const CASES = [
    ['not_shared', 'Not shared with you.'],
    ['portfolio_off', 'The portfolio service is switched off on the server.'],
    ['unreachable', 'The account cannot be read right now; prices still move.'],
  ] as const
  const unread = (part: 'positions' | 'holdings', state: string, words: string | null) =>
    ok(bookOf(part, [], { state: state as never, words, read_at: null }))
  const noZero = () => {
    const text = document.body.textContent ?? ''
    expect(text).not.toContain('No open positions')
    expect(text).not.toContain('No holdings')
    expect(text).not.toMatch(/₹\s?0(\.00)?/)
    expect(text).not.toMatch(/\b0 open/)
  }

  it.each(CASES)('Positions, %s: the service sentence is the card', async (state, words) => {
    routeFetch({ '/family/api/books/positions': unread('positions', state, words) })
    mount('/family/positions', <FamilyPositions />)
    expect((await screen.findByTestId('book-unread')).textContent).toBe(words)
    noZero()
  })

  it.each(CASES)('Holdings, %s: the service sentence is the card', async (state, words) => {
    routeFetch({ '/family/api/books/holdings': unread('holdings', state, words) })
    mount('/family/holdings', <FamilyHoldings />, '/family/holdings')
    expect((await screen.findByTestId('book-unread')).textContent).toBe(words)
    noZero()
  })

  it.each(
    CASES
  )('Dashboard, %s: no figure and no count, the sentence once', async (state, words) => {
    routeFetch({
      '/family/api/market': ok(market),
      '/family/api/books/positions': unread('positions', state, words),
    })
    mount('/family', <FamilyDashboard />, '/family')
    expect((await screen.findByTestId('glance-unread')).textContent).toBe(words)
    expect(screen.queryByTestId('glance-total')).toBeNull()
    expect(screen.getAllByText(words)).toHaveLength(1)
    noZero()
  })

  it('has a plain sentence where the service sent none', async () => {
    routeFetch({ '/family/api/books/positions': unread('positions', 'not_shared', null) })
    mount('/family/positions', <FamilyPositions />)
    expect((await screen.findByTestId('book-unread')).textContent).toBe('Not shared with you.')
  })

  it('still says No open positions for a read that found none', async () => {
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', [])) })
    mount('/family/positions', <FamilyPositions />)
    expect(await screen.findByText('No open positions')).toBeInTheDocument()
    expect(screen.queryByTestId('book-unread')).toBeNull()
  })

  it('still says No open positions when the market is closed and the last read found none', async () => {
    routeFetch({
      '/family/api/books/positions': ok(
        bookOf('positions', [], { state: 'closed', words: 'Market closed · figures from 15:40:12' })
      ),
    })
    mount('/family/positions', <FamilyPositions />)
    expect(await screen.findByText('No open positions')).toBeInTheDocument()
  })
})

// Review L4 (v0.12.0): "refresh on every order and every 45 s" was said while the market was
// closed, when no timed read runs. Fails on the old stamp.
describe('the read stamp', () => {
  it('promises the 45 s refresh only while a market is open', async () => {
    routeFetch({ '/family/api/books/positions': ok(bookOf('positions', [FULL_POSITION])) })
    mount('/family/positions', <FamilyPositions />)
    expect(
      await screen.findByText(/Positions from .* · refresh on every order and every 45 s/)
    ).toBeTruthy()
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'feed_state', state: 'closed', since: Date.now() })
    })
    expect(await screen.findByText(/Positions from .* · market closed/)).toBeTruthy()
    expect(screen.queryByText(/every 45 s/)).toBeNull()
  })

  it('says market closed for a read the service marks closed', async () => {
    routeFetch({
      '/family/api/books/holdings': ok(
        bookOf('holdings', [FULL_HOLDING], {
          state: 'closed',
          words: 'Market closed · figures from 15:40:12',
        })
      ),
    })
    mount('/family/holdings', <FamilyHoldings />, '/family/holdings')
    expect(await screen.findByText(/Holdings from .* · market closed/)).toBeTruthy()
    expect(screen.queryByText(/every 45 s/)).toBeNull()
  })
})
