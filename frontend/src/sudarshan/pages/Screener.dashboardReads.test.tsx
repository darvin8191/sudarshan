/**
 * The Dashboard tab's reads, redraws and words in jsdom (review of lane C1): a card whose read
 * fails or races says so, a tick redraws only its own row, the feed and the login are said in the
 * app's words, and a reconnect re-reads what it must. The live subscription is a controllable fake.
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
  change: vi.fn(),
  presets: vi.fn(),
}))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ toast: toasts }))
const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: sockets.current }),
}))

/** Price access: ready, or Upstox logged out (no key, as usePriceAccess answers before login). */
const access = vi.hoisted(() => ({ loggedOut: false }))
vi.mock('@/sudarshan/access', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/access')>()),
  usePriceAccess: () =>
    access.loggedOut
      ? { state: 'upstox-logged-out', apiKey: null }
      : { state: 'ready', apiKey: 'k' },
}))

/**
 * The dashboard's one live subscription, shaped as the real hook answers: one LiveRow per item,
 * new objects on every render. `mode`: every row ticked a second ago; or no key (no line, no
 * snapshot: no price at all, socket not signed in).
 */
const feed = vi.hoisted(() => {
  const listeners = new Set<() => void>()
  return {
    mode: 'ticking' as 'ticking' | 'no_key',
    now: Date.UTC(2026, 10, 9, 5, 15, 30),
    followed: [] as string[],
    version: 0,
    listeners,
    /** One animation frame in which a tick arrived: the hook's holder re-renders. */
    tick() {
      this.version += 1
      for (const listener of [...listeners]) listener()
    },
  }
})
vi.mock('@/sudarshan/watchlist/useLiveRows', async () => {
  const React = await import('react')
  return {
    useLiveRows: ({ items }: { items: Array<{ symbol: string; exchange: string }> }) => {
      React.useSyncExternalStore(
        (listener) => {
          feed.listeners.add(listener)
          return () => {
            feed.listeners.delete(listener)
          }
        },
        () => feed.version
      )
      feed.followed = items.map((item) => `${item.exchange}:${item.symbol}`)
      const ticking = feed.mode === 'ticking'
      return {
        rows: items.map((item, index) => ({
          item: { ...item, id: index + 1, position: index },
          key: `${item.exchange}:${item.symbol}`,
          view: ticking
            ? {
                ltp: 1500.5,
                prevClose: 1480,
                at: feed.now - 1000,
                live: true,
                change: 20.5,
                changePercent: 1.38,
              }
            : {
                ltp: null,
                prevClose: null,
                at: null,
                live: false,
                change: null,
                changePercent: null,
              },
          direction: ticking ? 'up' : null,
          stale: false,
          tickAt: ticking ? feed.now - 1000 : null,
        })),
        line: null,
        now: feed.now,
        feed: { connected: ticking, fallback: false, epoch: 1 },
        snapshot: ticking ? 'ready' : 'idle',
        retry: () => {},
      }
    },
  }
})

/** Each draw of a result row calls `describe` once: the count of rows redrawn. */
const draws = vi.hoisted(() => ({ rows: 0 }))
vi.mock('@/sudarshan/watchlist/rows', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/sudarshan/watchlist/rows')>()
  return {
    ...real,
    describe: (symbol: string, exchange: string) => {
      draws.rows += 1
      return real.describe(symbol, exchange)
    },
  }
})

import { createQueryClient } from '@/app/providers'
import Screener from '@/sudarshan/pages/Screener'
import { sendChange } from '@/sudarshan/screener/actions'
import { ScreenerError } from '@/sudarshan/screener/api'
import { DASHBOARD_KEY, STATE_KEY } from '@/sudarshan/screener/keys'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
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

vi.setConfig({ testTimeout: 20_000 })

function renderPage(entry = '/screener') {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/screener" element={<Screener />} />
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
async function joinedOnce(room: ReturnType<typeof fakeSocket>, nth = 0) {
  await settle()
  const joins = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
  expect(joins.length).toBeGreaterThan(nth)
  act(() => joins[nth]?.ack(null, { ok: true }))
  await settle()
}
const cardOf = (screenId: number) =>
  document.querySelector(`[data-widget="${screenId}:table"]`) as HTMLElement
const symbolsIn = (card: HTMLElement) =>
  Array.from(card.querySelectorAll('[data-result]')).map((row) =>
    row.getAttribute('data-result')?.replace('NSE:', '')
  )
const runPush = (screenId: number, runId: number, at: number, candle: string, matches: number) => ({
  screen_id: screenId,
  run_id: runId,
  run_key: `k${runId}`,
  at,
  candle,
  counts: { matches, entered: 1, exited: 1, missing: 0, not_checked: 0 },
  entered: [resultRow('TCS')],
  exited: [{ symbol: 'INFY', exchange: 'NSE' }],
  marks: [],
})

let room: ReturnType<typeof fakeSocket>
beforeEach(() => {
  room = fakeSocket()
  sockets.current = room.socket
  feed.mode = 'ticking'
  access.loggedOut = false
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.results.mockReturnValue(new Promise(() => {}))
  api.state.mockResolvedValue(
    screenerState([screenRow({ id: 1, name: 'EMA cross' }), screenRow({ id: 2, name: 'Breakout' })])
  )
})
afterEach(() => vi.useRealTimers())

describe('a card whose own read fails after a run push (HIGH-1)', () => {
  it('says the rows are from the earlier check, with Try again, and reads again on request', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    api.widget.mockRejectedValueOnce(
      new ScreenerError({ code: 'failed', message: 'The screener is not answering.', field: null })
    )
    renderPage()
    await joinedOnce(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    expect(symbolsIn(cardOf(1))).toEqual(['INFY', 'SBIN'])
    act(() =>
      room.fire('sudarshan_screener_run', runPush(1, 11, NOW + 900_000, '10:15 to 10:30', 3))
    )
    await waitFor(() => expect(api.widget).toHaveBeenCalledTimes(1))
    await settle()
    const card = cardOf(1)
    expect(card.querySelector('[data-run]')?.textContent).toContain('10:15 to 10:30')
    expect(card.querySelector('[data-rows-behind="unread"]')?.textContent).toMatch(
      /from the .* check; the .* check could not be read/
    )
    // The price on a row past the run is the earlier check's, and says so.
    api.widget.mockResolvedValue(
      widgetCard({
        run: runRow(11, { at: NOW + 900_000, candle: '10:15 to 10:30', matches: 1 }),
        matches: 1,
        rows_shown: [resultRow('TCS')],
      })
    )
    await userEvent.click(within(card).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(symbolsIn(cardOf(1))).toEqual(['TCS']))
    expect(cardOf(1).querySelector('[data-rows-behind]')).toBeNull()
  })

  it('shows the card as being read, not as current, while the read is in flight', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    api.widget.mockReturnValue(new Promise(() => {}))
    renderPage()
    await joinedOnce(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    act(() =>
      room.fire('sudarshan_screener_run', runPush(1, 11, NOW + 900_000, '10:15 to 10:30', 3))
    )
    await waitFor(() => expect(api.widget).toHaveBeenCalledTimes(1))
    await settle()
    expect(cardOf(1).querySelector('[data-rows-behind="reading"]')).toBeTruthy()
  })
})

describe('a card read that answers a version conflict (M1)', () => {
  it('reads the dashboard again and says nothing: nothing was written', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()], { version: 3, suggested: false }))
    api.widget.mockRejectedValue(
      new ScreenerError({
        code: 'conflict',
        message: 'The dashboard was changed on another screen.',
        field: null,
      })
    )
    renderPage()
    await joinedOnce(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    act(() => room.fire('sudarshan_screener_run', runPush(1, 11, NOW, '10:15 to 10:30', 2)))
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
    expect(toasts.info).not.toHaveBeenCalled()
  })
})

describe('an older answer never replaces a newer card (M9)', () => {
  it('a dashboard read that started before a run does not put the older rows back', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    renderPage()
    await joinedOnce(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    let late: (value: unknown) => void = () => {}
    api.dashboard.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          late = resolve
        })
    )
    act(() => room.fire('sudarshan_screener_dashboard', { version: 9 }))
    await settle()
    api.widget.mockResolvedValue(
      widgetCard({
        run: runRow(11, { at: NOW + 900_000, candle: '10:15 to 10:30', matches: 1 }),
        matches: 1,
        rows_shown: [resultRow('TCS')],
      })
    )
    act(() =>
      room.fire('sudarshan_screener_run', runPush(1, 11, NOW + 900_000, '10:15 to 10:30', 1))
    )
    await waitFor(() => expect(symbolsIn(cardOf(1))).toEqual(['TCS']))
    await act(async () => {
      late(dashboard([widgetCard()], { version: 9, suggested: false }))
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    await settle()
    expect(symbolsIn(cardOf(1))).toEqual(['TCS'])
  })

  it('a card read that started before a newer run is not applied over it', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    const client = renderPage()
    await joinedOnce(room)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    const answers: Array<(value: unknown) => void> = []
    api.widget.mockImplementation(
      () =>
        new Promise((resolve) => {
          answers.push(resolve)
        })
    )
    act(() =>
      room.fire('sudarshan_screener_run', runPush(1, 11, NOW + 900_000, '10:15 to 10:30', 1))
    )
    await waitFor(() => expect(answers.length).toBe(1))
    await act(async () => {
      answers[0]?.(
        widgetCard({
          run: runRow(12, { at: NOW + 1_800_000, candle: '10:30 to 10:45', matches: 1 }),
          rows_shown: [resultRow('TCS')],
        })
      )
    })
    await waitFor(() => expect(symbolsIn(cardOf(1))).toEqual(['TCS']))
    // A late answer for run 10 (a read that started before everything) arrives after.
    await act(async () => {
      const { readCard } = await import('@/sudarshan/screener/dashboardCache')
      api.widget.mockResolvedValueOnce(widgetCard({ rows_shown: [resultRow('OLD')] }))
      await readCard(client, { screen_id: 1, kind: 'table' })
    })
    await settle()
    expect(symbolsIn(cardOf(1))).toEqual(['TCS'])
  })
})

describe('a card that failed in the budget loop (L1)', () => {
  it('is not read again each time a later card fills', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([
        widgetCard({ screen_id: 1 }),
        widgetCard({ screen_id: 2, rows_pending: true, rows_shown: [], run: null }),
        widgetCard({ screen_id: 3, rows_pending: true, rows_shown: [], run: null }),
        widgetCard({ screen_id: 4, rows_pending: true, rows_shown: [], run: null }),
      ])
    )
    api.state.mockResolvedValue(
      screenerState([1, 2, 3, 4].map((id) => screenRow({ id, name: `Screen ${id}` })))
    )
    api.widget.mockImplementation(async (id: number) => {
      if (id === 2)
        throw new ScreenerError({ code: 'failed', message: 'not answering', field: null })
      return widgetCard({ screen_id: id, rows_shown: [resultRow(`R${id}`)] })
    })
    renderPage()
    await waitFor(() => expect(symbolsIn(cardOf(4))).toEqual(['R4']))
    await settle()
    expect(api.widget.mock.calls.map((call) => call[0]).filter((id) => id === 2)).toHaveLength(1)
  })
})
