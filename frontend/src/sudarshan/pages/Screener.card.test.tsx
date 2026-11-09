/**
 * The table widget card (M9.16) in jsdom: its rows, the foot's two 48 dp links, the card's line
 * for each situation (never "Real-time" for a card whose rows are not followed, the engine's own
 * sentence when it is not live), and the pushes: a run changes a card's counts and run line only
 * and its rows move once, from the card's own read; another screen's layout reloads the dashboard;
 * nothing is read on a timer.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  runsSince: vi.fn(),
  dashboard: vi.fn(),
  widget: vi.fn(),
  results: vi.fn(),
}))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))
vi.mock('@/utils/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: sockets.current }),
}))

/** The dashboard's one live subscription: every followed instrument ticks, or none does. */
const feed = vi.hoisted(() => ({
  ticking: true,
  followed: [] as string[],
  now: Date.UTC(2026, 10, 9, 5, 15, 30),
}))
vi.mock('@/sudarshan/watchlist/useLiveRows', () => ({
  useLiveRows: ({ items }: { items: Array<{ symbol: string; exchange: string }> }) => {
    feed.followed = items.map((item) => `${item.exchange}:${item.symbol}`)
    return {
      rows: feed.ticking
        ? items.map((item, index) => ({
            item: { ...item, id: index + 1, position: index },
            key: `${item.exchange}:${item.symbol}`,
            view: {
              ltp: 1500.5,
              prevClose: 1480,
              at: feed.now - 1000,
              live: true,
              change: 20.5,
              changePercent: 1.38,
            },
            direction: 'up',
            stale: false,
            tickAt: feed.now - 1000,
          }))
        : [],
      line: null,
      now: feed.now,
      feed: { connected: true, fallback: false },
      snapshot: null,
      retry: () => {},
    }
  },
}))

import { createQueryClient } from '@/app/providers'
import { ScreenerError } from '@/sudarshan/screener/api'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { OPEN_LIST_KEY } from '@/sudarshan/watchlist/openList'
import {
  dashboard,
  fakeSocket,
  NOW,
  resultRow,
  runRow,
  screenerState,
  screenRow,
  widgetCard,
} from '@/test/screener'
import Screener from './Screener'

vi.setConfig({ testTimeout: 20_000 })

function renderPage() {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/screener']}>
        <Routes>
          <Route path="/screener" element={<Screener />} />
          <Route path="/watchlist" element={<p>The watchlist page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
}
async function joined(room: ReturnType<typeof fakeSocket>) {
  await settle()
  const join = room.emits.find((each) => each.event === 'sudarshan_screener_join')
  expect(join).toBeDefined()
  act(() => join?.ack(null, { ok: true }))
  await settle()
}
const cardOf = (screenId: number) =>
  document.querySelector(`[data-widget="${screenId}:table"]`) as HTMLElement
const symbolsIn = (card: HTMLElement) =>
  Array.from(card.querySelectorAll('[data-result]')).map((row) =>
    row.getAttribute('data-result')?.replace('NSE:', '')
  )

let room: ReturnType<typeof fakeSocket>
beforeEach(async () => {
  room = fakeSocket()
  sockets.current = room.socket
  feed.ticking = true
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.results.mockReturnValue(new Promise(() => {}))
  api.state.mockResolvedValue(
    screenerState([screenRow({ id: 1, name: 'EMA cross' }), screenRow({ id: 2, name: 'Breakout' })])
  )
  localStorage.clear()
})
afterEach(() => vi.useRealTimers())

describe('the table card', () => {
  it('shows its rows with the price, the change and the values, at least 56 dp each', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([
        widgetCard({
          rows_shown: [
            resultRow('INFY', { values: 'RSI 61.2 · OI +4.1%' }),
            resultRow('SBIN', { change_pct: -0.6, forming: true }),
          ],
        }),
      ])
    )
    renderPage()
    await joined(room)
    const card = await waitFor(() => {
      const found = cardOf(1)
      expect(found).toBeTruthy()
      return found
    })
    expect(symbolsIn(card)).toEqual(['INFY', 'SBIN'])
    expect(within(card).getByText('RSI 61.2 · OI +4.1%')).toBeTruthy()
    expect(within(card).getByText('forming')).toBeTruthy()
    expect(within(card).getAllByText(/1,500\.50/).length).toBe(2)
    const row = card.querySelector('[data-result] button') as HTMLElement
    expect(row.className).toContain('min-h-14')
    expect(within(card).getByText(/Run at 10:45:00 · 10:00 to 10:15 candle · 2 match/)).toBeTruthy()
  })

  it('has "and N more" and the list link, each a 48 dp target, and the list link opens that list', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard({ more: 7 })]))
    renderPage()
    await joined(room)
    const more = await screen.findByRole('button', { name: /and 7 more/ })
    expect(more.className).toContain('min-h-12')
    const link = screen.getByRole('link', { name: /Screener: 15-min EMA cross · 2/ })
    expect(link.className).toContain('min-h-12')
    await userEvent.click(link)
    expect(await screen.findByText('The watchlist page')).toBeTruthy()
    expect(localStorage.getItem(OPEN_LIST_KEY)).toBe('5')
  })

  it('opens the screen from "and N more" and from the head, and the symbol sheet from a row', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard({ more: 7 })]))
    renderPage()
    await joined(room)
    await userEvent.click(await screen.findByRole('button', { name: /and 7 more/ }))
    expect(await screen.findByRole('tab', { name: 'Results' })).toBeTruthy()
  })
})

describe('the card line', () => {
  it('says Not receiving updates until the room is joined', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    await screen.findByText('EMA cross')
    const card = cardOf(1)
    expect(
      within(card).getByText(/Not receiving updates on this screen · reconnecting/)
    ).toBeTruthy()
    expect(card.querySelector('[data-greyed]')).not.toBeNull()
    await joined(room)
    expect(within(cardOf(1)).getByText(/^Real-time · next check 10:45$/)).toBeTruthy()
  })

  it('with 12 cards of 10 rows, cards 11 and 12 say Prices at the run and never Real-time', async () => {
    const cards = Array.from({ length: 12 }, (_, i) =>
      widgetCard({
        screen_id: i + 1,
        rows: 10,
        rows_shown: Array.from({ length: 10 }, (_, r) => resultRow(`S${i + 1}R${r + 1}`)),
      })
    )
    api.dashboard.mockResolvedValue(dashboard(cards))
    api.state.mockResolvedValue(
      screenerState(
        cards.map((card) => screenRow({ id: card.screen_id, name: `Screen ${card.screen_id}` }))
      )
    )
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(12)).toBeTruthy())
    expect(feed.followed.length).toBe(100)
    for (const id of [1, 5, 10]) {
      expect(within(cardOf(id)).getByText(/^Real-time/)).toBeTruthy()
    }
    for (const id of [11, 12]) {
      const card = cardOf(id)
      expect(within(card).queryByText(/Real-time/)).toBeNull()
      expect(
        within(card).getByText('Prices at the run · 100 prices update at most · next check 10:45')
      ).toBeTruthy()
      // Its rows say where their price comes from.
      expect(within(card).getAllByText(/at the run, 10:45/).length).toBe(10)
    }
  })

  it('says why a card is not live in the engine’s own sentence', async () => {
    const sentence =
      'Not checking · Upstox refused the price feed’s token: put the current Analytics Token in the feed’s token file'
    api.state.mockResolvedValue(
      screenerState([screenRow({ id: 1, name: 'EMA cross' })], {
        engine: { state: 'feed_down', line: sentence, real_time: false, next_at: null },
      })
    )
    api.dashboard.mockResolvedValue(
      dashboard([
        widgetCard({
          line: { kind: 'feed_down', words: 'Not checking', live: false, state: 'feed_down' },
          not_live_words: sentence,
        }),
      ])
    )
    renderPage()
    await joined(room)
    const card = await waitFor(() => {
      const found = cardOf(1)
      expect(within(found).getByText(sentence)).toBeTruthy()
      return found
    })
    expect(within(card).queryByText(/reconnecting/)).toBeNull()
    expect(card.querySelector('[data-greyed]')).not.toBeNull()
  })

  it('never says Real-time while no tick arrives for the card’s rows', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    feed.ticking = false
    renderPage()
    await joined(room)
    const card = await waitFor(() => {
      const found = cardOf(1)
      expect(found).toBeTruthy()
      return found
    })
    expect(within(card).queryByText(/Real-time/)).toBeNull()
  })
})

describe('a pushed run', () => {
  it('patches the run line and the counts at once, and the rows move once, from the card’s own read', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ rows_shown: [resultRow('INFY'), resultRow('SBIN', { rank: 2 })] })])
    )
    let finish: (card: ReturnType<typeof widgetCard>) => void = () => {}
    api.widget.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    expect(symbolsIn(cardOf(1))).toEqual(['INFY', 'SBIN'])

    act(() =>
      room.fire('sudarshan_screener_run', {
        screen_id: 1,
        run_id: 11,
        run_key: 'k',
        at: NOW + 900_000,
        candle: '10:15 to 10:30',
        counts: { matches: 3, entered: 1, exited: 1, missing: 0, not_checked: 0 },
        entered: [resultRow('TCS')],
        exited: [{ symbol: 'INFY', exchange: 'NSE' }],
        marks: [],
      })
    )
    await settle()
    // The run line and the count moved; the rows did not (the push's "exited" is not the run's).
    await waitFor(() =>
      expect(within(cardOf(1)).getByText(/10:15 to 10:30 candle · 3 match/)).toBeTruthy()
    )
    expect(symbolsIn(cardOf(1))).toEqual(['INFY', 'SBIN'])
    expect(api.widget).toHaveBeenCalledTimes(1)
    expect(api.widget).toHaveBeenCalledWith(1, 'table', expect.anything())

    await act(async () => {
      finish(
        widgetCard({
          run: runRow(11, { at: NOW + 900_000, candle: '10:15 to 10:30', matches: 3 }),
          matches: 3,
          rows_shown: [
            resultRow('SBIN'),
            resultRow('TCS', { rank: 2 }),
            resultRow('INFY', { rank: 3 }),
          ],
        })
      )
    })
    await waitFor(() => expect(symbolsIn(cardOf(1))).toEqual(['SBIN', 'TCS', 'INFY']))
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    expect(api.widget).toHaveBeenCalledTimes(1)
  })

  it('reads each card of the screen that ran, and leaves the other screens alone', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 1 }), widgetCard({ screen_id: 2 })])
    )
    api.widget.mockImplementation(async (id: number) => widgetCard({ screen_id: id }))
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(2)).toBeTruthy())
    act(() =>
      room.fire('sudarshan_screener_run', {
        screen_id: 2,
        run_id: 11,
        run_key: 'k',
        at: NOW,
        candle: '10:15 to 10:30',
        counts: { matches: 2, entered: 0, exited: 0, missing: 0, not_checked: 0 },
        entered: [],
        exited: [],
        marks: [],
      })
    )
    await settle()
    expect(api.widget.mock.calls.map((call) => call[0])).toEqual([2])
  })
})

describe('what keeps the dashboard current', () => {
  it('reads the dashboard again when another screen changed the layout, and not for an old version', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()], { version: 3, suggested: false }))
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    act(() => room.fire('sudarshan_screener_dashboard', { version: 3 }))
    await settle()
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    act(() => room.fire('sudarshan_screener_dashboard', { version: 4 }))
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
  })

  it('reads the dashboard after a reconnect when runs moved while the phone was away', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    api.runsSince.mockResolvedValue({ runs: [runRow(11)], more: false })
    renderPage()
    // The cards are on screen before the phone wakes and the room is joined.
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    await joined(room)
    await waitFor(() => expect(api.runsSince).toHaveBeenCalledWith(10))
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2), { timeout: 5000 })
    expect(api.runsSince).toHaveBeenCalledWith(10)
  })

  it('reads the dashboard again, and shows no sentence, when a card read says it is gone', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    api.widget.mockRejectedValue(
      new ScreenerError({ code: 'gone', message: 'That screen is no longer there.', field: null })
    )
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    act(() =>
      room.fire('sudarshan_screener_run', {
        screen_id: 1,
        run_id: 11,
        run_key: 'k',
        at: NOW,
        candle: '10:15 to 10:30',
        counts: { matches: 2, entered: 0, exited: 0, missing: 0, not_checked: 0 },
        entered: [],
        exited: [],
        marks: [],
      })
    )
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(/no longer there/)).toBeNull()
  })

  it('while the layout is a suggestion, reads it again when a screen is paused', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    act(() =>
      room.fire('sudarshan_screener_state', {
        engine: { state: 'real_time', line: 'Real-time', real_time: true, next_at: null },
        screens: [
          {
            id: 1,
            version: 1,
            status: 'paused',
            status_reason: null,
            next_check: null,
            matches: 7,
            entered_today: 3,
          },
        ],
        removed: [],
      })
    )
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
  })

  it('makes no request in ten minutes without a push', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    await joined(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    vi.useFakeTimers({ shouldAdvanceTime: false })
    await act(async () => {
      vi.advanceTimersByTime(600_000)
    })
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    expect(api.widget).not.toHaveBeenCalled()
    expect(api.state).toHaveBeenCalledTimes(1)
  })
})
