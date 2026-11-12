/**
 * The dashboard's reads after a join (review L1): a join re-reads the whole dashboard only when the
 * socket was away after the page's last dashboard read began, and then shares a read in flight; a
 * window focus never reads it; the catch-up shares the page's own state read. The order of events
 * is fixed in each test, so the count of reads is the same on any machine.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, waitFor } from '@testing-library/react'
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
vi.mock('@/utils/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: sockets.current }),
}))
vi.mock('@/sudarshan/access', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/access')>()),
  usePriceAccess: () => ({ state: 'ready', apiKey: 'k' }),
}))
vi.mock('@/sudarshan/watchlist/useLiveRows', () => ({
  useLiveRows: ({ items }: { items: Array<{ symbol: string; exchange: string }> }) => ({
    rows: items.map((item, index) => ({
      item: { ...item, id: index + 1, position: index },
      key: `${item.exchange}:${item.symbol}`,
      view: {
        ltp: null,
        prevClose: null,
        at: null,
        live: false,
        change: null,
        changePercent: null,
      },
      direction: null,
      stale: false,
      tickAt: null,
    })),
    line: null,
    now: Date.UTC(2026, 10, 9, 5, 15, 30),
    feed: { connected: true, fallback: false, epoch: 1 },
    snapshot: 'ready',
    retry: () => {},
  }),
}))

import { createQueryClient } from '@/app/providers'
import Screener from '@/sudarshan/pages/Screener'
import { sendChange } from '@/sudarshan/screener/actions'
import { STATE_KEY } from '@/sudarshan/screener/keys'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { dashboard, fakeSocket, screenerState, screenRow, widgetCard } from '@/test/screener'

vi.setConfig({ testTimeout: 20_000 })

function renderPage() {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/screener']}>
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
    for (let i = 0; i < 20; i += 1) await Promise.resolve()
  })
  // let TanStack's setTimeout-scheduled notifications and React's re-render run too
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}
const cardOf = (screenId: number) =>
  document.querySelector(`[data-widget="${screenId}:table"]`) as HTMLElement | null

let room: ReturnType<typeof fakeSocket>
beforeEach(() => {
  room = fakeSocket()
  sockets.current = room.socket
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.results.mockReturnValue(new Promise(() => {}))
  api.state.mockResolvedValue(
    screenerState([screenRow({ id: 1, name: 'EMA cross' }), screenRow({ id: 2, name: 'Breakout' })])
  )
})
afterEach(() => vi.useRealTimers())

describe('the join catch-up and the dashboard read', () => {
  it('a join that finds nothing new after the page has read the dashboard reads nothing more', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    renderPage()
    // the REST reads land first (a cold phone open: the socket joins a second later)
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    await settle()
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    // now the join is acknowledged: catchUp finds no run after the one the state named
    const joins = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
    expect(joins.length).toBe(1)
    act(() => joins[0]?.ack(null, { ok: true }))
    await settle()
    await settle()
    expect(api.runsSince).toHaveBeenCalledTimes(1)
    // The page was never away, nothing moved, and the layout is the one just read.
    // (v0.7.0 read the whole dashboard a second time here.)
    expect(api.dashboard).toHaveBeenCalledTimes(1)
  })

  it('exactly one more read after a Pause, when the join lands after the first read', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 2, name: 'Breakout' }), widgetCard({ screen_id: 1 })])
    )
    const client = renderPage()
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    await settle()
    const joins = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
    act(() => joins[0]?.ack(null, { ok: true }))
    await settle()
    const before = api.dashboard.mock.calls.length
    api.change.mockResolvedValue(
      screenRow({ id: 1, name: 'EMA cross', status: 'paused', version: 2 })
    )
    const row = client
      .getQueryData<{ screens: Array<{ id: number }> }>(STATE_KEY)
      ?.screens.find((each) => each.id === 1)
    await act(async () => {
      await sendChange(client, row as never, 'pause')
    })
    act(() =>
      room.fire('sudarshan_screener_state', {
        engine: { state: 'real_time', line: 'Real-time', real_time: true, next_at: null },
        screens: [
          {
            id: 1,
            version: 2,
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
    await settle()
    await settle()
    // The pause itself costs exactly one read; the variable part was always before it.
    expect(api.dashboard.mock.calls.length - before).toBe(1)
    // The mount's read and the pause's: no third from the join.
    expect(api.dashboard).toHaveBeenCalledTimes(2)
  })

  it('a reconnect reads the dashboard once more, and a read already in flight is shared, not cancelled', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    renderPage()
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    await settle()
    const first = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
    act(() => first[0]?.ack(null, { ok: true }))
    await settle()
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    // The phone wakes: the socket left and comes back, and the catch-up lands while the page's own
    // read of the dashboard (a slow one) is still out.
    let release: (value: ReturnType<typeof dashboard>) => void = () => {}
    api.dashboard.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      })
    )
    act(() => room.fire('disconnect'))
    act(() => room.fire('connect'))
    const joins = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
    act(() => joins[1]?.ack(null, { ok: true }))
    await settle()
    await settle()
    expect(api.runsSince).toHaveBeenCalledTimes(2)
    expect(api.dashboard).toHaveBeenCalledTimes(2)
    // A second catch-up while that read is out joins it (cancelRefetch is off).
    act(() => room.fire('disconnect'))
    act(() => room.fire('connect'))
    const again = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
    act(() => again[2]?.ack(null, { ok: true }))
    await settle()
    await settle()
    expect(api.dashboard).toHaveBeenCalledTimes(2)
    release(dashboard([widgetCard()]))
    await settle()
    expect(api.dashboard).toHaveBeenCalledTimes(2)
  })

  it('a window focus does not read the dashboard: the reconnect covers a wake', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    renderPage()
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    await settle()
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(Date.now() + 61_000) // past the dashboard's staleTime
    act(() => {
      document.dispatchEvent(new Event('visibilitychange', { bubbles: true }))
    })
    await settle()
    expect(api.dashboard).toHaveBeenCalledTimes(1)
  })

  it('a join that lands while the page’s own state read is out shares it, not a second read', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard()]))
    let release: (value: ReturnType<typeof screenerState>) => void = () => {}
    api.state.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      })
    )
    renderPage()
    await settle()
    const joins = room.emits.filter((each) => each.event === 'sudarshan_screener_join')
    act(() => joins[0]?.ack(null, { ok: true }))
    await settle()
    await settle()
    expect(api.state).toHaveBeenCalledTimes(1)
    release(screenerState([screenRow({ id: 1, name: 'EMA cross' })]))
    await waitFor(() => expect(cardOf(1)).toBeTruthy())
    expect(api.state).toHaveBeenCalledTimes(1)
  })
})
