/**
 * F16 on the page: ranked lists from Upstox (M8.9). The All lists groups; a ranked list read once on
 * opening, locked, with its figure on the second line; the age line and its stale style; no timed
 * read (fake timers across 30 minutes); a return after 15 s, a pull and the button, a hidden page;
 * rows that move only at a read; each state in the server's words; the underlying chip; Make a copy
 * in the order shown; the 500 ms guard; the switch off. The bridge is the v2 mock.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
import type { Ranked, Row } from '@/sudarshan/watchlist/v2/types'
import { feed } from '@/test/marketDataHarness'
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const NOW = Date.UTC(2026, 10, 13, 5, 13, 10) // 10:43:10 IST
const REF = 'ranked:options:INDEX:OI_GAINERS'
const ENTRIES = [
  'ranked:futures:INDEX:TOP_TRADED',
  'ranked:options:INDEX:OI_GAINERS',
  'ranked:options:INDEX:UNDER_5000',
]

const FIGURE: Record<string, string> = {
  NIFTY25NOV2625950CE: 'OI +12.4%',
  BANKNIFTY25NOV2657000CE: 'OI +9.1%',
  NIFTY25NOV2626000CE: 'OI +8.2%',
  RELIANCE25NOV261300PE: 'OI +5.0%',
  NIFTY25NOV2626050PE: 'OI +20.3%',
}

const contract = (symbol: string): Row => ({
  item: null,
  contract: {
    exchange: 'NFO',
    symbol,
    key: `NFO:${symbol}`,
    name: symbol,
    lot: 75,
    tick: 0.05,
    expiry: '2026-11-25',
    isin: null,
  },
  section_id: null,
  flag: null,
  has_note: false,
  expired: false,
  metric: FIGURE[symbol] ?? null,
})

const A = 'NIFTY25NOV2625950CE'
const B = 'BANKNIFTY25NOV2657000CE'
const C = 'NIFTY25NOV2626000CE'
const D = 'RELIANCE25NOV261300PE'
const NEW = 'NIFTY25NOV2626050PE'

function ranked(over: Partial<Ranked> = {}): Ranked {
  return {
    ref: REF,
    state: 'ok',
    made_at: NOW - 125_000, // 10:41:05, read 10:43:10
    read_at: NOW,
    rows: [A, B, C, D].map(contract),
    left_out: 0,
    words: 'Ranked by Upstox · 2 min ago',
    ...over,
  }
}

const names = ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'].map((name, i) => ({
  mark: i + 1,
  name,
}))
const state = (favourites: unknown[] = [], enabled = true) => ({
  enabled,
  versions: { lists: 'a:1', favourites: 1, flags: 1 },
  flag_names: names,
  favourites,
})
const entry = (ref: string) => ({
  ref,
  kind: 'ranked',
  name: ref,
  count: null,
  room: null,
  locked: true,
  note: null,
})
const fav = { slot: 1, ref: REF, kind: 'ranked', name: 'Index options · OI gainers', count: null }

function mount(path = '/watchlist') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/autologin" element={<p>Auto login page</p>} />
          <Route path="/market-alerts" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function Where() {
  const at = useLocation()
  return <p data-testid="where">{`${at.pathname}${at.search}`}</p>
}

function visibility(value: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value })
  document.dispatchEvent(new Event('visibilitychange'))
}

/**
 * Pull the page down from its top, as a finger does: from the ranked list unless told where, 140 px
 * down and `sideways` px across.
 */
function pullDown(from?: Element | Window, sideways = 0) {
  const start = from ?? (document.querySelector('[data-ranked-line]') as Element)
  const at = (x: number, y: number) => ({
    touches: [{ clientX: x, clientY: y }],
    changedTouches: [{ clientX: x, clientY: y }],
  })
  fireEvent.touchStart(start, at(100, 10))
  fireEvent.touchMove(start, at(100 + sideways, 150))
  fireEvent.touchEnd(start, {
    touches: [],
    changedTouches: [{ clientX: 100 + sideways, clientY: 150 }],
  })
}

const advance = (ms: number) => act(async () => void vi.advanceTimersByTime(ms))
const rankedCalls = () => b().count('ranked')
const shownOrder = () =>
  [...document.querySelectorAll('li button[aria-haspopup="dialog"]')].map(
    (el) => /BANKNIFTY|RELIANCE|NIFTY \d+/.exec(el.textContent ?? '')?.[0]
  )

async function openRanked() {
  await screen.findByRole('tab', { name: /Index options · OI gainers/ })
  fireEvent.click(screen.getByRole('tab', { name: /Index options · OI gainers/ }))
  await screen.findByText(/Ranked by Upstox · /)
}

beforeEach(() => {
  b().reset()
  feed.reset()
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
  b().on('state', () => state([fav]))
  b().on('lists', () => ({
    version: 'a:1',
    own: [
      { ref: 'list:1', kind: 'list', name: 'Main', count: 1, room: 249, locked: false, note: null },
    ],
    presets: [],
    ranked: ENTRIES.map(entry),
    flags: [],
    favourites: [fav],
  }))
  b().on('ranked', () => ranked())
  b().on('symbol', () => ({
    key: `NFO:${A}`,
    flag: null,
    flag_name: null,
    note: null,
    note_at: null,
    expired: false,
    version: null,
    lists: [],
  }))
  visibility('visible')
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
  visibility('visible')
})

describe('ranked lists in All lists', () => {
  it('show only what the server offers, by Futures and Options, then Index, Stock and Commodity', async () => {
    mount('/watchlist?sheet=lists')
    const block = await screen.findByRole('region', { name: 'Ranked by Upstox' })
    expect(within(block).getByText('Futures · Index')).toBeInTheDocument()
    expect(within(block).getByText('Options · Index')).toBeInTheDocument()
    expect(within(block).queryByText('Options · Stock')).toBeNull()
    expect(within(block).queryByText(/MTF/)).toBeNull()
    expect(within(block).getByRole('button', { name: /^Top traded/ })).toHaveTextContent(
      'Ranked by Upstox · Index futures'
    )
    expect(within(block).getByRole('button', { name: /^Under ₹5,000 to buy/ })).toBeInTheDocument()
  })
})

describe('a ranked list on the page', () => {
  it('reads once on opening, shows its figure on the second line and is locked', async () => {
    mount()
    await openRanked()
    expect(rankedCalls()).toBe(1)
    expect(b().calls.find((call) => call.op === 'ranked')?.params).toMatchObject({
      src: 'options',
      asset: 'INDEX',
      cat: 'OI_GAINERS',
    })
    expect(screen.getByText('Ranked by Upstox · 2 min ago')).toBeInTheDocument()
    await screen.findByText('OI +12.4%')
    expect(shownOrder()).toEqual(['NIFTY 25950', 'BANKNIFTY', 'NIFTY 26000', 'RELIANCE'])
    // No edit control at all.
    expect(screen.queryByRole('button', { name: 'Add' })).toBeNull()
    await userEvent
      .setup({ advanceTimers: vi.advanceTimersByTime })
      .click(screen.getByRole('button', { name: 'List options' }))
    await screen.findByRole('menuitem', { name: 'New list' })
    for (const gone of [/Rename/, /Reorder or remove/, /Delete list/, /New section/]) {
      expect(screen.queryByRole('menuitem', { name: gone })).toBeNull()
    }
    // F17: the alert is offered disabled, with its sentence.
    expect(screen.getByRole('menuitem', { name: /Alert on this list/ })).toHaveAttribute(
      'aria-disabled',
      'true'
    )
    expect(
      screen.getByText(
        'A ranked list changes with every read. Set the alert on a list or a built-in list instead.'
      )
    ).toBeInTheDocument()
  })

  it('never calls the ranking or the list Live', async () => {
    mount()
    await openRanked()
    expect(screen.getByRole('region', { name: /ranked by Upstox/i }).textContent).not.toMatch(
      /live/i
    )
  })

  it('shows both times on a tap on the age line', async () => {
    mount()
    await openRanked()
    fireEvent.click(screen.getByRole('button', { name: /Ranked by Upstox · 2 min ago/ }))
    expect(await screen.findByText('Ranking made 10:41:05, read 10:43:10')).toBeInTheDocument()
  })
})

describe('when a ranking is read', () => {
  it('never reads on a timer: thirty minutes with the list open make no request, and the age grows', async () => {
    mount()
    await openRanked()
    expect(rankedCalls()).toBe(1)
    await advance(30 * 60_000)
    expect(rankedCalls()).toBe(1)
    const line = screen.getByRole('button', { name: /Ranked by Upstox · 32 min ago/ })
    // The stale style is an icon and words, never colour alone.
    expect(line).toHaveTextContent('Pull down to read it again')
    expect(line).toHaveAttribute('data-age-line', 'stale')
    expect(line.querySelector('.lucide-clock')).not.toBeNull()
  })

  it('keeps the fresh style, with no clock, under ten minutes', async () => {
    mount()
    await openRanked()
    await advance(5 * 60_000)
    const line = screen.getByRole('button', { name: /Ranked by Upstox · 7 min ago/ })
    expect(line).toHaveAttribute('data-age-line', 'fresh')
    expect(line).not.toHaveTextContent('Pull down')
  })

  it('reads once on a return after more than 15 s, and nothing on a return within 15 s', async () => {
    mount()
    await openRanked()
    visibility('hidden')
    await advance(10_000)
    visibility('visible')
    expect(rankedCalls()).toBe(1)
    visibility('hidden')
    await advance(20_000)
    visibility('visible')
    await waitFor(() => expect(rankedCalls()).toBe(2))
    await advance(1000)
    expect(rankedCalls()).toBe(2)
  })

  it('reads nothing while the page is hidden, and the read it owes is made when it is shown', async () => {
    visibility('hidden')
    mount()
    await screen.findByRole('tab', { name: /Index options · OI gainers/ })
    fireEvent.click(screen.getByRole('tab', { name: /Index options · OI gainers/ }))
    await advance(30 * 60_000)
    expect(rankedCalls()).toBe(0)
    visibility('visible')
    await screen.findByText(/Ranked by Upstox · /)
    expect(rankedCalls()).toBe(1)
  })

  it('answers a pull inside a minute with the cached read, and reads again after it', async () => {
    mount()
    await openRanked()
    await advance(20_000)
    pullDown()
    expect(await screen.findByText(/Read 20 s ago/, { selector: 'output' })).toBeInTheDocument()
    expect(rankedCalls()).toBe(1)
    // The button is off for the minute and says the same.
    const button = screen.getByRole('button', { name: /Read 2\d s ago/ })
    expect(button).toBeDisabled()
    expect(button.getAttribute('aria-label') ?? button.textContent).toMatch(/Read 2\d s ago/)
    await advance(41_000)
    const again = await screen.findByRole('button', { name: 'Refresh ranking' })
    expect(again).toBeEnabled()
    pullDown()
    await waitFor(() => expect(rankedCalls()).toBe(2))
  })

  // Review M1: Chrome's own pull-to-reload is the same gesture, and a pull under a sheet is not one.
  it('holds off the browser’s own pull-to-reload while a ranked list is on show, and gives it back', async () => {
    const root = document.documentElement
    root.style.overscrollBehaviorY = ''
    mount()
    await screen.findByRole('tab', { name: /Main/ })
    expect(root.style.overscrollBehaviorY).toBe('')
    await openRanked()
    expect(root.style.overscrollBehaviorY).toBe('contain')
    expect(document.body.style.overscrollBehaviorY).toBe('contain')
    // Back to his own list, through All lists (a ranked favourite open, Main has no tab).
    fireEvent.click(screen.getByRole('button', { name: /All lists/ }))
    const own = await screen.findByRole('region', { name: 'Your lists' })
    fireEvent.click(within(own).getByRole('button', { name: /^Main/ }))
    await waitFor(() => expect(root.style.overscrollBehaviorY).toBe(''))
    expect(document.body.style.overscrollBehaviorY).toBe('')
  })

  it('takes a pull only on the list: not in an open sheet, not outside the list, not sideways', async () => {
    mount()
    await openRanked()
    await screen.findByText('OI +12.4%')
    await advance(61_000) // past the minute in which a pull shows the last read
    // A sheet open over the list: a drag in it is the sheet's.
    fireEvent.click(document.querySelector('li button[aria-haspopup="dialog"]') as Element)
    const sheet = await screen.findByRole('dialog')
    pullDown(sheet)
    pullDown(sheet.querySelector('button') as Element)
    await advance(100)
    expect(rankedCalls()).toBe(1)
    fireEvent.keyDown(sheet, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // Outside the list (the page's title, the window), and a drag that is mostly sideways.
    pullDown(screen.getByRole('heading', { name: 'Watchlist' }))
    pullDown(window)
    pullDown(undefined, 120)
    await advance(100)
    expect(rankedCalls()).toBe(1)
    // On the rows themselves, it reads.
    pullDown(document.querySelector('li button[aria-haspopup="dialog"]') as Element)
    await waitFor(() => expect(rankedCalls()).toBe(2))
  })

  it('reads from the Refresh ranking button once its minute has passed', async () => {
    mount()
    await openRanked()
    await advance(61_000)
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh ranking' }))
    await waitFor(() => expect(rankedCalls()).toBe(2))
  })

  it('moves the rows at a read only, never on a tick', async () => {
    mount()
    await openRanked()
    await screen.findByText('OI +12.4%')
    const before = shownOrder()
    await act(async () => {
      feed.push(D, 'NFO', { ltp: 900, open: 100, high: 950, low: 100, close: 100 })
      feed.push(B, 'NFO', { ltp: 5, open: 100, high: 100, low: 4, close: 100 })
    })
    await advance(1000)
    expect(shownOrder()).toEqual(before)
    expect(rankedCalls()).toBe(1)
  })

  it('says what is new for a minute after a read that changed the order', async () => {
    mount()
    await openRanked()
    await screen.findByText('OI +12.4%')
    b().on('ranked', () => ranked({ rows: [NEW, A, B, C].map(contract), read_at: NOW + 70_000 }))
    await advance(70_000)
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh ranking' }))
    expect(
      await screen.findByText('Ranking updated 10:44 · 1 new: NIFTY 26,050 PE')
    ).toBeInTheDocument()
    expect(shownOrder()).toEqual(['NIFTY 26050', 'NIFTY 25950', 'BANKNIFTY', 'NIFTY 26000'])
    await advance(61_000)
    expect(screen.queryByText(/Ranking updated/)).toBeNull()
  })
})

describe('the states, in the server’s words', () => {
  it('signin: the sentence, no rows, and a Sign in button to the broker login', async () => {
    const said =
      'Sign in to Upstox to read rankings. Ranked lists use your daily Upstox sign-in, which has not been done today.'
    b().on('ranked', () =>
      ranked({ state: 'signin', made_at: null, read_at: null, rows: [], words: said })
    )
    mount()
    await screen.findByRole('tab', { name: /Index options · OI gainers/ })
    fireEvent.click(screen.getByRole('tab', { name: /Index options · OI gainers/ }))
    expect(await screen.findByText(said)).toBeInTheDocument()
    expect(screen.queryByText('This list is empty')).toBeNull()
    fireEvent.click(screen.getByRole('link', { name: 'Sign in' }))
    expect(await screen.findByText('Auto login page')).toBeInTheDocument()
  })

  it('wait: the sentence, the old rows stay with their age', async () => {
    b().on('ranked', () =>
      ranked({ state: 'wait', words: 'Upstox asked us to wait. Pull down again in a minute.' })
    )
    mount()
    await openRanked()
    expect(
      screen.getByText('Upstox asked us to wait. Pull down again in a minute.')
    ).toBeInTheDocument()
    expect(screen.getByText('Ranked by Upstox · 2 min ago')).toBeInTheDocument()
    expect(shownOrder()).toHaveLength(4)
  })

  it('unavailable: the old rows stay, labelled with their age and the server’s sentence', async () => {
    const said = 'Upstox’s ranking could not be read just now. Showing the ranking from 10:41.'
    b().on('ranked', () => ranked({ state: 'unavailable', words: said }))
    mount()
    await openRanked()
    expect(screen.getByText(said)).toBeInTheDocument()
    expect(screen.getByText('Ranked by Upstox · 2 min ago')).toBeInTheDocument()
    expect(shownOrder()).toHaveLength(4)
  })

  it('closed: the market-closed sentence over the last ranking', async () => {
    const said = 'Market closed · ranking from 15:29 yesterday'
    b().on('ranked', () => ranked({ state: 'closed', words: said }))
    mount()
    await screen.findByRole('tab', { name: /Index options · OI gainers/ })
    fireEvent.click(screen.getByRole('tab', { name: /Index options · OI gainers/ }))
    expect(await screen.findByText(said)).toBeInTheDocument()
    expect(shownOrder()).toHaveLength(4)
  })

  it('stale: the stale style and the way out, even with the age under ten minutes by the phone’s clock', async () => {
    b().on('ranked', () => ranked({ state: 'stale', made_at: NOW - 700_000 }))
    mount()
    await openRanked()
    const line = screen.getByRole('button', { name: /Ranked by Upstox · 11 min ago/ })
    expect(line).toHaveTextContent('Pull down to read it again')
  })

  it('the left-out sentence follows the age line', async () => {
    const left = "2 contracts from Upstox are not in today's contract list and are left out."
    b().on('ranked', () => ranked({ left_out: 2, words: `Ranked by Upstox · 2 min ago ${left}` }))
    mount()
    await openRanked()
    expect(screen.getByText(left)).toBeInTheDocument()
  })

  it('a failed read says the server’s sentence and keeps no rows it never had', async () => {
    b().on('ranked', () => ({
      status: 'error',
      code: 'failed',
      message: 'Today’s contract list is not loaded yet.',
    }))
    mount()
    await screen.findByRole('tab', { name: /Index options · OI gainers/ })
    fireEvent.click(screen.getByRole('tab', { name: /Index options · OI gainers/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Today’s contract list is not loaded yet.'
    )
  })
})

describe('the underlying chip', () => {
  it('filters the rows on the phone and says how many of the top are that underlying', async () => {
    mount()
    await openRanked()
    await screen.findByText('OI +12.4%')
    fireEvent.click(screen.getByRole('button', { name: /^NIFTY 2$/ }))
    expect(await screen.findByText('2 of the top 4 are NIFTY contracts')).toBeInTheDocument()
    expect(shownOrder()).toEqual(['NIFTY 25950', 'NIFTY 26000'])
    expect(rankedCalls()).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: /^NIFTY 2$/ }))
    expect(shownOrder()).toHaveLength(4)
  })
})

describe('Make a copy', () => {
  it('sends every key in the order shown, shows the server’s words and opens the new list', async () => {
    b().on('copy', () => {
      own.lists = [
        ...own.lists,
        {
          id: 2,
          name: 'Index options · OI gainers (my copy)',
          position: 1,
          items: [],
        },
      ]
      return {
        list: 2,
        added: 2,
        skipped: 0,
        version: '1',
        words: '2 symbols are not in today’s contract list and were left out of the copy.',
      }
    })
    mount()
    await openRanked()
    await screen.findByText('OI +12.4%')
    fireEvent.click(screen.getByRole('button', { name: /^NIFTY 2$/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Make a copy' }))
    const name = await screen.findByRole('textbox', { name: 'List name' })
    expect(name).toHaveValue('Index options · OI gainers (my copy)')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('copy')).toBe(1))
    expect(b().calls.find((call) => call.op === 'copy')?.body).toEqual({
      from: { ref: REF, keys: [`NFO:${A}`, `NFO:${C}`] },
      to: { name: 'Index options · OI gainers (my copy)' },
    })
    await waitFor(() =>
      expect(toasts.success).toHaveBeenCalledWith(
        '2 symbols are not in today’s contract list and were left out of the copy.'
      )
    )
    expect(
      await screen.findByRole('tab', { name: /Index options · OI gainers \(my copy\)/ })
    ).toHaveAttribute('aria-selected', 'true')
  })

  it('takes no tap for the first half second after it opens', async () => {
    b().on('copy', () => ({ list: 2, added: 4, skipped: 0, version: '1', words: null }))
    mount()
    await openRanked()
    await screen.findByText('OI +12.4%')
    const make = await screen.findByRole('button', { name: 'Make a copy' })
    OPEN_GUARD.ms = 500
    fireEvent.click(make)
    const save = await screen.findByRole('button', { name: 'Save' })
    fireEvent.click(save)
    expect(b().count('copy')).toBe(0)
    await new Promise((resolve) => setTimeout(resolve, 600))
    fireEvent.click(save)
    await waitFor(() => expect(b().count('copy')).toBe(1))
  })
})

describe('with the switch off', () => {
  it('has no ranked lists and makes no ranked read', async () => {
    b().on('state', () => state([], false))
    mount('/watchlist?sheet=lists')
    await screen.findByRole('tab', { name: /Main/ })
    expect(screen.queryByRole('region', { name: 'Ranked by Upstox' })).toBeNull()
    expect(screen.queryByRole('button', { name: /All lists/ })).toBeNull()
    expect(b().count('ranked')).toBe(0)
    expect(b().count('lists')).toBe(0)
  })
})
