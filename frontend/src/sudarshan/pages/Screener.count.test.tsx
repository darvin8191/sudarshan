/**
 * The count widget (M9.16, lane C3) in jsdom: the matches now as one big figure, the day's
 * entries and exits, the run line, the strip of matches at each check with the service's own
 * sentence for a screen reader, and a line that says Checking and never Real-time.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  runsSince: vi.fn(),
  dashboard: vi.fn(),
  widget: vi.fn(),
  presets: vi.fn(),
  universes: vi.fn(),
}))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))
vi.mock('@/utils/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('@/sudarshan/watchlist/useLiveRows', () => ({
  useLiveRows: () => ({
    rows: [],
    line: null,
    now: Date.UTC(2026, 10, 9, 5, 15, 30),
    feed: { connected: true, fallback: false },
    snapshot: null,
    retry: () => {},
  }),
}))
const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: sockets.current }),
}))

import { createQueryClient } from '@/app/providers'
import { cardLine } from '@/sudarshan/screener/cardLine'
import { patchRun } from '@/sudarshan/screener/dashboardCache'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import type { RunPush } from '@/sudarshan/screener/types'
import {
  dashboard,
  fakeSocket,
  NOW,
  REAL_TIME,
  runRow,
  screenerState,
  screenRow,
  widgetCard,
} from '@/test/screener'
import Screener from './Screener'

vi.setConfig({ testTimeout: 15_000 })

const WORDS = 'Matches at each check today: 0, 2, 3, 5, 12; latest 12 at 11:15.'
const counting = (over = {}) =>
  widgetCard({
    kind: 'count',
    rows_shown: [],
    matches: 1234,
    day_only: false,
    line: {
      kind: 'checking',
      words: 'Checking · next check 10:45',
      live: true,
      state: 'real_time',
    },
    counts: {
      entered_today: 3,
      exited_today: 1,
      series: [0, 2, 3, 5, 12].map((matches, i) => ({ at: NOW + i * 900_000, matches })),
      series_words: WORDS,
    },
    ...over,
  })

let room: ReturnType<typeof fakeSocket>
async function joined() {
  await waitFor(() =>
    expect(room.emits.find((each) => each.event === 'sudarshan_screener_join')).toBeDefined()
  )
  const join = room.emits.find((each) => each.event === 'sudarshan_screener_join')
  act(() => join?.ack(null, { ok: true }))
}

function renderPage() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={['/screener']}>
        <Routes>
          <Route path="/screener" element={<Screener />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  room = fakeSocket()
  sockets.current = room.socket
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.state.mockResolvedValue(screenerState([screenRow({ id: 1, name: 'One' })]))
})

describe('the count card', () => {
  it('shows the matches now in Indian grouping, the entries and exits, and the run line', async () => {
    api.dashboard.mockResolvedValue(dashboard([counting()]))
    renderPage()
    const card = (await screen.findByText('One')).closest('li') as HTMLElement
    expect(within(card).getByText('1,234')).toBeTruthy()
    expect(within(card).getByText('match now')).toBeTruthy()
    expect(card.textContent).toContain('+3 entered')
    expect(card.textContent).toContain('−1 exited today')
    expect(card.querySelectorAll('svg[aria-hidden="true"]').length).toBeGreaterThan(2)
    expect(card.textContent).toContain('Run at')
  })

  it('draws one bar for each check, labels the latest, and gives a reader the service sentence', async () => {
    api.dashboard.mockResolvedValue(dashboard([counting()]))
    renderPage()
    const card = (await screen.findByText('One')).closest('li') as HTMLElement
    expect(card.querySelectorAll('[data-bar]').length).toBe(5)
    expect(within(card).getByRole('img', { name: WORDS })).toBeTruthy()
    expect(card.querySelector('[data-latest]')?.textContent).toMatch(/^12 at /)
  })

  it('says Checking with the next check, never Real-time, and shows the mode chip for a day-only screen', async () => {
    api.dashboard.mockResolvedValue(dashboard([counting({ day_only: true, mode: 'forming_day' })]))
    renderPage()
    const card = (await screen.findByText('One')).closest('li') as HTMLElement
    await joined()
    await waitFor(() =>
      expect(card.querySelector('[data-card-line]')?.textContent).toMatch(/^Checking/)
    )
    expect(card.textContent).not.toContain('Real-time')
    expect(within(card).getByText('Forming day candle')).toBeTruthy()
  })

  it('has the options button at 48 dp', async () => {
    api.dashboard.mockResolvedValue(dashboard([counting()]))
    renderPage()
    const button = await screen.findByRole('button', { name: 'Widget options for One' })
    expect(button.className).toContain('size-12')
  })
})

describe('the count line and points', () => {
  const screenOf = (card: ReturnType<typeof counting>) => screenRow({ id: card.screen_id })
  const feed = { followed: 0, line: null, now: NOW }

  it('says Checking even when the state has moved and the engine is real-time', () => {
    const card = counting({
      line: { kind: 'real_time', words: 'x', live: true, state: 'real_time' },
    })
    const view = cardLine({
      card,
      screen: screenOf(card),
      engine: REAL_TIME,
      hearing: 'joined',
      feed,
    })
    expect(view.icon).toBe('circle-check')
    expect(view.words).toMatch(/^Checking/)
    expect(view.words).not.toContain('Real-time')
  })

  it('appends the pushed run as one point, once, and leaves an older run alone', () => {
    const card = counting({ run: runRow(10, { at: NOW + 3_600_000 }) })
    const held = dashboard([card])
    const push = {
      screen_id: card.screen_id,
      run_id: 11,
      counts: { matches: 9, not_checked: 0 },
    } as RunPush
    const run = runRow(11, { at: NOW + 7_200_000, matches: 9 })
    const next = patchRun(held, push, run)
    expect(next?.widgets[0].counts?.series.at(-1)).toEqual({ at: run.at, matches: 9 })
    expect(next?.widgets[0].counts?.series.length).toBe(6)
    const again = patchRun(next as never, push, run)
    expect(again?.widgets[0].counts?.series.length).toBe(6)
  })
})
