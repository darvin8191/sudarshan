// SUDARSHAN P6 D5: the viewer's Orders, Trades and shared Watchlist screens, through the real
// components with a fake service and a fake socket. Synthetic rows only. Every request a screen
// makes must go to /family/api (or the socket).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PRICE_SCALE, QTY_SCALE } from '@/sudarshan/portfolio/types'
import { COST_FIELDS } from './books'
import { resetFamilyManagerForTests } from './familyManager'
import { ORDER_FIELD_CATEGORY, ORDER_NEVER_HIDDEN, orderFigures, tradeFigures } from './ordersView'
import FamilyOrders from './pages/FamilyOrders'
import FamilyTrades from './pages/FamilyTrades'
import FamilyWatchlist from './pages/FamilyWatchlist'
import { FamilyMeContext } from './session'
import { bookOf, FakeSocket, fail, meOf, ok, routeFetch } from './testing/fixtures'
import { mountFamily } from './testing/harness'

const px = (rupees: number) => Math.round(rupees * PRICE_SCALE)
const qty = (units: number) => Math.round(units * QTY_SCALE)
const AT = Date.UTC(2026, 10, 13, 5, 30, 0) // 11:00:00 in India

const OPEN_ORDER = {
  order_id: 'ord-open',
  exchange: 'NSE',
  symbol: 'SYNTH',
  instrument_key: 'NSE_EQ|SYNTH',
  product: 'MIS',
  order_type: 'LIMIT',
  transaction_type: 'BUY',
  status: 'open',
  status_message: null,
  qty_u: qty(65),
  filled_qty_u: qty(0),
  pending_qty_u: qty(65),
  price_u: px(168),
  trigger_price_u: null,
  avg_price_u: null,
  placed_at: AT,
}
const DONE_ORDER = {
  ...OPEN_ORDER,
  order_id: 'ord-done',
  symbol: 'DONEX',
  order_type: 'MARKET',
  transaction_type: 'SELL',
  status: 'complete',
  price_u: null,
  avg_price_u: px(170),
  filled_qty_u: qty(65),
  pending_qty_u: qty(0),
  placed_at: AT - 60_000,
}
const REJECTED_ORDER = {
  ...OPEN_ORDER,
  order_id: 'ord-rej',
  symbol: 'REJEX',
  status: 'rejected',
  status_message: 'Insufficient funds for this order',
  placed_at: AT - 120_000,
}
// With quantities hidden the mask removes every quantity and price field of an order.
const MASKED_ORDER = {
  order_id: 'ord-masked',
  exchange: 'NSE',
  symbol: 'MASKD',
  product: 'MIS',
  order_type: 'LIMIT',
  transaction_type: 'BUY',
  status: 'open',
  status_message: null,
  placed_at: AT - 30_000,
}
const MASKED_DONE = {
  ...MASKED_ORDER,
  order_id: 'ord-masked-done',
  symbol: 'MASKX',
  status: 'complete',
}

const TRADE = {
  trade_id: 'trd-1',
  order_id: 'ord-done',
  exchange: 'NSE',
  symbol: 'DONEX',
  product: 'MIS',
  order_type: 'MARKET',
  transaction_type: 'SELL',
  qty_u: qty(65),
  price_u: px(170),
  amount_paise: 1_105_000,
  placed_at: AT,
  exchange_at: AT,
}
const MASKED_TRADE = {
  trade_id: 'trd-2',
  order_id: 'ord-masked',
  exchange: 'NSE',
  symbol: 'MASKD',
  product: 'MIS',
  order_type: 'LIMIT',
  transaction_type: 'BUY',
  placed_at: AT + 1_000,
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const FORBIDDEN =
  /^(exit|exit all|add|buy|buy more|sell|place|cancel(?!led)|cancel all|modify|square)/i
const onlyFamily = (urls: string[]) => urls.every((url) => url.startsWith('/family/api/'))

// A card is itself a button that opens its sheet; its text starts with the side chip (BUY or
// SELL), which is a label and not an action. Every other button is looked at by its name.
function noActions(root: HTMLElement) {
  for (const button of within(root).queryAllByRole('button')) {
    if (button.closest('[data-order]')) continue
    expect(button.getAttribute('aria-label') ?? button.textContent ?? '').not.toMatch(FORBIDDEN)
  }
  expect(within(root).queryAllByRole('link')).toHaveLength(0)
}

const BOOK_ORDERS = '/family/api/books/orders'
const BOOK_TRADES = '/family/api/books/trades'

describe('Orders', () => {
  it('shows each status as an icon and a word, with the side and the figures the mask leaves', async () => {
    const router = routeFetch({
      [BOOK_ORDERS]: ok(bookOf('orders', [OPEN_ORDER, DONE_ORDER, REJECTED_ORDER])),
    })
    mountFamily('/family/orders', <FamilyOrders />)
    const open = await screen.findByRole('button', { name: /SYNTH/ })
    expect(open.textContent).toContain('OPEN')
    expect(open.textContent).toContain('BUY')
    expect(open.textContent).toContain('65')
    expect(open.textContent).toContain('Limit 168.00')
    const done = screen.getByRole('button', { name: /DONEX/ })
    expect(done.textContent).toContain('COMPLETE')
    expect(done.textContent).toContain('SELL')
    expect(done.textContent).toContain('65/65')
    expect(done.textContent).toContain('Avg 170.00')
    const rejected = screen.getByRole('button', { name: /REJEX/ })
    expect(rejected.textContent).toContain('REJECTED')
    // an icon beside every status word: never colour alone
    for (const card of [open, done, rejected]) {
      expect(card.querySelector('[data-status] svg')).not.toBeNull()
    }
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('shows a masked order as Hidden, never as 0, and keeps its status and side', async () => {
    routeFetch({
      [BOOK_ORDERS]: ok(bookOf('orders', [MASKED_ORDER, MASKED_DONE], { hidden: ['pnl', 'qty'] })),
    })
    mountFamily('/family/orders', <FamilyOrders />)
    const card = await screen.findByRole('button', { name: /MASKD/ })
    expect(card.textContent).toContain('Hidden')
    expect(card.textContent).toContain('OPEN')
    expect(card.textContent).toContain('BUY')
    expect(card.textContent).not.toMatch(/(^|[^\d.,])0(\.00)?($|[^\d])/)
    expect(screen.getByRole('button', { name: /MASKX/ }).textContent).toContain('COMPLETE')
    expect(screen.getByRole('button', { name: /MASKX/ }).textContent).toContain('Hidden')
    // the figures helpers agree: absent is undefined, never 0
    const figures = orderFigures(MASKED_ORDER as never)
    expect(Object.values(figures).every((value) => value === undefined)).toBe(true)
  })

  it('has no Modify, Cancel or Cancel all, in the list or the sheet, and no link to an owner page', async () => {
    const router = routeFetch({
      [BOOK_ORDERS]: ok(bookOf('orders', [OPEN_ORDER, REJECTED_ORDER])),
    })
    const { container } = mountFamily(
      '/family/orders?act=cancel&row=ord-open',
      <FamilyOrders />,
      '/family/orders'
    )
    await screen.findByRole('button', { name: /SYNTH/ })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    noActions(container)
    await userEvent.click(screen.getByRole('button', { name: /SYNTH/ }))
    const sheet = await screen.findByRole('dialog')
    noActions(sheet)
    expect(within(sheet).getByText('Order ID')).toBeTruthy()
    expect(router.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it("says the broker's reason on a refused order", async () => {
    routeFetch({ [BOOK_ORDERS]: ok(bookOf('orders', [REJECTED_ORDER])) })
    mountFamily('/family/orders', <FamilyOrders />)
    await userEvent.click(await screen.findByRole('button', { name: /REJEX/ }))
    const reason = await screen.findByTestId('rejection-reason')
    expect(reason.textContent).toContain('Broker says:')
    expect(reason.textContent).toContain('Insufficient funds for this order')
  })

  it('changes a status at once when the service says it read the orders again (Y11)', async () => {
    let status = 'open'
    const router = routeFetch({
      [BOOK_ORDERS]: () => ok(bookOf('orders', [{ ...OPEN_ORDER, status }])),
    })
    mountFamily('/family/orders', <FamilyOrders />)
    const card = await screen.findByRole('button', { name: /SYNTH/ })
    expect(card.textContent).toContain('OPEN')
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    const before = router.urls().filter((url) => url === BOOK_ORDERS).length
    status = 'complete'
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'book', part: 'orders', read_at: Date.now() })
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /SYNTH/ }).textContent).toContain('COMPLETE')
    )
    expect(router.urls().filter((url) => url === BOOK_ORDERS).length).toBe(before + 1)
    // a book message about another part changes nothing
    act(() => FakeSocket.last().say({ type: 'book', part: 'trades', read_at: Date.now() }))
    expect(router.urls().filter((url) => url === BOOK_ORDERS).length).toBe(before + 1)
  })

  it('filters by status and counts them', async () => {
    routeFetch({
      [BOOK_ORDERS]: ok(bookOf('orders', [OPEN_ORDER, DONE_ORDER, REJECTED_ORDER])),
    })
    mountFamily('/family/orders', <FamilyOrders />)
    await screen.findByRole('button', { name: /SYNTH/ })
    expect(screen.getByRole('button', { name: /^All\s*3$/ })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /^Rejected\s*1$/ }))
    expect(screen.queryByRole('button', { name: /SYNTH/ })).toBeNull()
    expect(screen.getByRole('button', { name: /REJEX/ })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /^Cancelled\s*0$/ }))
    expect(screen.getByText('No cancelled orders')).toBeTruthy()
  })

  it('shows the last price of a working order from the socket, and labels a stopped feed', async () => {
    routeFetch({ [BOOK_ORDERS]: ok(bookOf('orders', [OPEN_ORDER, DONE_ORDER])) })
    mountFamily('/family/orders', <FamilyOrders />)
    await screen.findByRole('button', { name: /SYNTH/ })
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'auth', status: 'success' })
      FakeSocket.last().say({
        type: 'market_data',
        symbol: 'SYNTH',
        exchange: 'NSE',
        mode: 1,
        data: {
          ltp: 171.5,
          change: 1,
          change_percent: 0.5,
          close: 170,
          ltt: 1,
          timestamp: Date.now(),
        },
      })
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /SYNTH/ }).textContent).toContain('171.50')
    )
    // a finished order carries no live price
    expect(screen.getByRole('button', { name: /DONEX/ }).textContent).not.toContain('LTP')
    act(() => FakeSocket.last().say({ type: 'feed_state', state: 'closed', since: Date.now() }))
    expect(screen.queryByText(/^Live$/)).toBeNull()
  })

  it('says in the service words when the read is not fresh, and the read time', async () => {
    routeFetch({
      [BOOK_ORDERS]: ok(
        bookOf('orders', [OPEN_ORDER], {
          state: 'unreachable',
          words: 'The account cannot be read right now; prices still move. Figures from 10:41:05.',
          read_at: AT,
        })
      ),
    })
    mountFamily('/family/orders', <FamilyOrders />)
    expect(await screen.findByText(/cannot be read right now; prices still move/)).toBeTruthy()
    expect(screen.getByText(/Orders from 11:00:00/)).toBeTruthy()
  })

  it('reads nothing for a member whose mask hides orders', async () => {
    const router = routeFetch({ [BOOK_ORDERS]: ok(bookOf('orders', [OPEN_ORDER])) })
    mountFamily(
      '/family/orders',
      <FamilyMeContext.Provider value={meOf('child')}>
        <FamilyOrders />
      </FamilyMeContext.Provider>
    )
    expect(await screen.findByText('Not shared with you.')).toBeTruthy()
    expect(router.calls).toHaveLength(0)
  })

  it("shows the service's not-shared answer as the sentence, not as an empty book", async () => {
    routeFetch({
      [BOOK_ORDERS]: ok(
        bookOf('orders', [], {
          state: 'not_shared',
          words: 'Not shared with you.',
          hidden: ['orders'],
        })
      ),
    })
    mountFamily('/family/orders', <FamilyOrders />)
    expect(await screen.findByText('Not shared with you.')).toBeTruthy()
    expect(screen.queryByText('No orders today')).toBeNull()
  })

  it("says a failed read in the viewer's words with a retry", async () => {
    routeFetch(
      { [BOOK_ORDERS]: fail('busy', 'The family view is busy. Wait a minute, then try again.') },
      { [BOOK_ORDERS]: 503 }
    )
    mountFamily('/family/orders', <FamilyOrders />)
    expect(await screen.findByText(/The family view is busy/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Try again|Retry/i })).toBeTruthy()
  })
})

describe('Trades', () => {
  it('lists fills newest first with the side, quantity, price and value', async () => {
    const router = routeFetch({
      [BOOK_TRADES]: ok(
        bookOf('trades', [
          { ...TRADE, trade_id: 'old', exchange_at: AT - 9_000, symbol: 'OLDER' },
          TRADE,
        ])
      ),
    })
    mountFamily('/family/trades', <FamilyTrades />)
    await screen.findByText(/2 trades/)
    const rows = screen.getAllByRole('listitem').filter((item) => item.hasAttribute('data-trade'))
    expect(rows.map((row) => row.getAttribute('data-trade'))).toEqual(['trd-1', 'old'])
    expect(rows[0].textContent).toContain('SELL')
    expect(rows[0].textContent).toMatch(/65\s*at\s*170\.00/)
    expect(rows[0].textContent).toContain('11,050')
    expect(screen.getByText(/2 trades · 0 buy · 2 sell/)).toBeTruthy()
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('shows a masked fill as Hidden, never as 0, and has no link out', async () => {
    routeFetch({ [BOOK_TRADES]: ok(bookOf('trades', [MASKED_TRADE], { hidden: ['pnl', 'qty'] })) })
    const { container } = mountFamily('/family/trades', <FamilyTrades />)
    const row = await waitFor(() => {
      const found = container.querySelector('[data-trade="trd-2"]')
      if (!found) throw new Error('not yet')
      return found as HTMLElement
    })
    expect(row.textContent).toContain('Hidden')
    expect(row.textContent).toContain('BUY')
    expect(row.textContent).not.toMatch(/(^|[^\d.,])0(\.00)?($|[^\d])/)
    noActions(container)
    expect(Object.values(tradeFigures(MASKED_TRADE as never)).every((v) => v === undefined)).toBe(
      true
    )
  })

  it('refetches the trades when the service says it read them again', async () => {
    const router = routeFetch({ [BOOK_TRADES]: ok(bookOf('trades', [TRADE])) })
    mountFamily('/family/trades', <FamilyTrades />)
    await screen.findByText(/1 trade ·/)
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    const before = router.urls().filter((url) => url === BOOK_TRADES).length
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'book', part: 'trades', read_at: Date.now() })
    })
    await waitFor(() =>
      expect(router.urls().filter((url) => url === BOOK_TRADES).length).toBe(before + 1)
    )
  })

  it('reads nothing for a member whose mask hides orders', async () => {
    const router = routeFetch({ [BOOK_TRADES]: ok(bookOf('trades', [TRADE])) })
    mountFamily(
      '/family/trades',
      <FamilyMeContext.Provider value={meOf('child')}>
        <FamilyTrades />
      </FamilyMeContext.Provider>
    )
    expect(await screen.findByText('Not shared with you.')).toBeTruthy()
    expect(router.calls).toHaveLength(0)
  })
})

describe('ORDER_FIELD_CATEGORY against the one privacy table', () => {
  const table = JSON.parse(
    readFileSync(resolve(__dirname, '../../../../sudarshan/family/privacy_fields.json'), 'utf8')
  ) as { categories: Record<string, string[]> }
  const hides = (category: string, field: string) =>
    (table.categories[category] ?? []).some((entry) =>
      entry.startsWith('*') ? field.endsWith(entry.slice(1)) : entry === field
    )

  it.each(Object.entries(ORDER_FIELD_CATEGORY))('%s is hidden by %s', (field, category) => {
    expect(hides(category, field)).toBe(true)
  })
  it.each(ORDER_NEVER_HIDDEN)('%s is hidden by no category', (field) => {
    for (const category of Object.keys(table.categories)) expect(hides(category, field)).toBe(false)
  })
  it('every price and amount of an order or a trade goes with P&L too (review H1)', () => {
    const money = Object.keys(ORDER_FIELD_CATEGORY).filter((f) => !f.endsWith('qty_u'))
    expect(money.sort()).toEqual(['amount_paise', 'avg_price_u', 'price_u', 'trigger_price_u'])
    for (const field of money) expect(COST_FIELDS).toContain(field)
  })
})

const LISTS = [
  { id: 1, name: 'Shared one', count: 2 },
  { id: 2, name: 'Shared two', count: 1 },
]
const LIST_ONE = {
  id: 1,
  name: 'Shared one',
  refreshed_at: AT,
  rows: [
    { symbol: 'LISTA', exchange: 'NSE', name: 'List A Ltd' },
    { symbol: 'LISTB', exchange: 'NSE', name: 'List B Ltd' },
  ],
}

describe('Watchlist', () => {
  function service(extra: Record<string, unknown> = {}) {
    return routeFetch({
      '/family/api/watchlists/1': ok(LIST_ONE),
      '/family/api/watchlists/2': ok({
        id: 2,
        name: 'Shared two',
        refreshed_at: AT,
        rows: [{ symbol: 'LISTC', exchange: 'NSE', name: 'List C Ltd' }],
      }),
      '/family/api/watchlists': ok(LISTS),
      ...extra,
    })
  }

  it('says what it is, shows the first list, and switches lists', async () => {
    const router = service()
    mountFamily('/family/watchlist', <FamilyWatchlist />)
    expect(screen.getByText('Lists the account owner shares with you')).toBeTruthy()
    expect(await screen.findByText('LISTA')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Shared one/ }).getAttribute('aria-pressed')).toBe(
      'true'
    )
    await userEvent.click(screen.getByRole('button', { name: /Shared two/ }))
    expect(await screen.findByText('LISTC')).toBeTruthy()
    expect(screen.queryByText('LISTA')).toBeNull()
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('has no add, reorder, Buy, Sell, option chain, depth or chart', async () => {
    const router = service()
    const { container } = mountFamily('/family/watchlist', <FamilyWatchlist />)
    await screen.findByText('LISTA')
    noActions(container)
    expect(screen.queryByRole('button', { name: /chart|depth|option|arrange|reorder/i })).toBeNull()
    expect(router.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('says so when no list is shared', async () => {
    routeFetch({ '/family/api/watchlists': ok([]) })
    mountFamily('/family/watchlist', <FamilyWatchlist />)
    expect(
      await screen.findByText('No list is shared with you yet. Ask the account owner.')
    ).toBeTruthy()
  })

  it("moves a row's price with the socket, and never says Real-time or Live without a tick", async () => {
    service()
    mountFamily('/family/watchlist', <FamilyWatchlist />)
    const row = await screen.findByText('LISTA')
    expect(screen.queryByText(/^Live$/)).toBeNull()
    expect(screen.queryByText(/Real-time/)).toBeNull()
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'auth', status: 'success' })
      FakeSocket.last().say({
        type: 'market_data',
        symbol: 'LISTA',
        exchange: 'NSE',
        mode: 1,
        data: {
          ltp: 250.25,
          change: 5,
          change_percent: 2,
          close: 245.25,
          ltt: 1,
          timestamp: Date.now(),
        },
      })
    })
    await waitFor(() => expect(row.closest('li')?.textContent).toContain('250.25'))
    await waitFor(() => expect(screen.getByText(/Real-time/)).toBeTruthy())
  })

  it('labels a stopped feed instead of showing old prices as current', async () => {
    service()
    mountFamily('/family/watchlist', <FamilyWatchlist />)
    await screen.findByText('LISTA')
    await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0))
    act(() => {
      FakeSocket.last().open()
      FakeSocket.last().say({ type: 'feed_state', state: 'closed', since: Date.now() })
    })
    await waitFor(() => expect(screen.getByText(/market closed|closed/i)).toBeTruthy())
    expect(screen.queryByText(/^Live$/)).toBeNull()
  })

  it('past the symbol cap a row says it is not real-time, in place of a price', async () => {
    const rows = Array.from({ length: 151 }, (_, i) => ({
      symbol: `CAP${String(i).padStart(3, '0')}`,
      exchange: 'NSE',
      name: `Cap ${i}`,
    }))
    routeFetch({
      '/family/api/watchlists/1': ok({ id: 1, name: 'Big', refreshed_at: AT, rows }),
      '/family/api/watchlists': ok([{ id: 1, name: 'Big', count: 151 }]),
    })
    const { container } = mountFamily('/family/watchlist', <FamilyWatchlist />)
    await waitFor(() => expect(container.querySelectorAll('[data-symbol]').length).toBe(151), {
      timeout: 10_000,
    })
    const rowOf = (symbol: string) =>
      container.querySelector(`[data-symbol="${symbol}"]`)?.textContent ?? ''
    expect(rowOf('CAP150')).toContain('Not real-time · too many symbols open')
    expect(rowOf('CAP149')).not.toContain('Not real-time')
    expect(rowOf('CAP000')).not.toContain('Not real-time')
  }, 20_000)

  it("re-reads a list's membership once a minute, on a clock only the test moves", async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const router = service()
    mountFamily('/family/watchlist', <FamilyWatchlist />)
    await screen.findByText('LISTA')
    const reads = () => router.urls().filter((url) => url === '/family/api/watchlists/1').length
    const first = reads()
    await act(async () => {
      vi.advanceTimersByTime(59_000)
    })
    expect(reads()).toBe(first)
    await act(async () => {
      vi.advanceTimersByTime(2_000)
    })
    await waitFor(() => expect(reads()).toBeGreaterThan(first))
  })

  it("says a failed read in the viewer's words", async () => {
    routeFetch(
      { '/family/api/watchlists': fail('off', 'The family view is switched off on the server.') },
      { '/family/api/watchlists': 503 }
    )
    mountFamily('/family/watchlist', <FamilyWatchlist />)
    expect(await screen.findByText('The family view is switched off on the server.')).toBeTruthy()
  })
})
