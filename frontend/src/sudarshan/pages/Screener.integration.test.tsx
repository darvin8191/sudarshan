/**
 * The Screener page with the service's optional fields (integration): alerts words and the pause
 * with its way back, the engine line in the service's own words and only "Real-time" while the
 * page hears the screener, the next check, the budget, a run's extra facts, a past run's note, and
 * what a preview needs. The bridge is replaced at screenerApi with the README's exact shapes.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { type InitialEntry, MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  runsSince: vi.fn(),
  create: vi.fn(),
  change: vi.fn(),
  remove: vi.fn(),
  results: vi.fn(),
  runs: vi.fn(),
  run: vi.fn(),
  preview: vi.fn(),
  presets: vi.fn(),
  universes: vi.fn(),
  today: vi.fn(),
  membership: vi.fn(),
  member: vi.fn(),
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
vi.mock('@/utils/toast', () => ({ toast: toasts, showToast: toasts }))

const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: sockets.current }),
}))

import { createQueryClient } from '@/app/providers'
import { resetScreenActionsForTests } from '@/sudarshan/screener/actions'
import { RunFacts } from '@/sudarshan/screener/History'
import { PreviewBody } from '@/sudarshan/screener/PreviewSheet'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { RunSheet } from '@/sudarshan/screener/RunSheet'
import { fakeSocket, NOW, REAL_TIME, runRow, screenerState, screenRow } from '@/test/screener'
import Screener from './Screener'

function renderPage(entries: InitialEntry[]) {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Routes>
          <Route path="/screener" element={<Screener />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}

const NOT_LINKED = {
  state: 'feed_down',
  line: 'Not checking · the screener is not connected to the price feed yet',
  real_time: false,
  next_at: null,
} as const

beforeEach(() => {
  sockets.current = null
  resetScreenActionsForTests()
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
})
afterEach(() => vi.useRealTimers())

describe('the engine line', () => {
  it('reads the service words without the feed link, never Real-time', async () => {
    api.state.mockResolvedValue(screenerState([screenRow()], { engine: NOT_LINKED }))
    renderPage(['/screener?tab=screens'])
    expect(await screen.findByText(NOT_LINKED.line)).toBeInTheDocument()
    expect(screen.queryByText(/Real-time/)).not.toBeInTheDocument()
  })

  it('does not say Real-time while the page is not hearing the screener', async () => {
    api.state.mockResolvedValue(screenerState([screenRow()], { engine: REAL_TIME }))
    sockets.current = fakeSocket().socket
    renderPage(['/screener?tab=screens'])
    await screen.findByText(/Not receiving updates/)
    expect(screen.queryByText(/Real-time/)).not.toBeInTheDocument()
  })

  it('says Real-time once the room is joined', async () => {
    api.state.mockResolvedValue(screenerState([screenRow()], { engine: REAL_TIME }))
    const room = fakeSocket()
    sockets.current = room.socket
    renderPage(['/screener?tab=screens'])
    await screen.findByText('15-min EMA cross')
    await act(async () => {
      for (let i = 0; i < 10; i += 1) await Promise.resolve()
    })
    act(() =>
      room.emits.find((each) => each.event === 'sudarshan_screener_join')?.ack(null, { ok: true })
    )
    expect(await screen.findByText(/Real-time/)).toBeInTheDocument()
  })

  it('adds the next check when the line does not say it', async () => {
    api.state.mockResolvedValue(
      screenerState([screenRow()], {
        engine: { state: 'closed', line: 'Market closed', real_time: false, next_at: NOW },
      })
    )
    renderPage(['/screener?tab=screens'])
    expect(await screen.findByText(/Market closed · next check 10:45/)).toBeInTheDocument()
  })

  it('says how much of the room the screens use', async () => {
    api.state.mockResolvedValue(
      screenerState([screenRow()], { limits: { active: 5, active_used: 1, max_keys: 700 } })
    )
    renderPage(['/screener?tab=screens'])
    expect(
      await screen.findByText(
        /1 of 5 screens checking through the day · prices followed for up to 700 instruments/
      )
    ).toBeInTheDocument()
  })
})

describe('alerts on a screen', () => {
  it('shows the service words on the card, an icon with them', async () => {
    api.state.mockResolvedValue(
      screenerState([screenRow({ alerts: 'Alerts on', alerts_paused: false })])
    )
    renderPage(['/screener?tab=screens'])
    const card = (await screen.findByText('Alerts on')).closest('[data-alerts]') as HTMLElement
    expect(card.dataset.alerts).toBe('text')
    expect(card.querySelector('svg')).not.toBeNull()
  })

  it('shows a pause in words and turns alerts back on once', async () => {
    const paused = screenRow({
      alerts: 'Alerts paused after the first alert',
      alerts_paused: true,
      version: 3,
    })
    api.state.mockResolvedValue(screenerState([paused]))
    api.change.mockResolvedValue({
      ...paused,
      alerts: 'Alerts on',
      alerts_paused: false,
      version: 4,
    })
    renderPage(['/screener?screen=1&tab=edit'])
    expect(await screen.findByText('Alerts paused after the first alert')).toBeInTheDocument()
    const turnOn = screen.getByRole('button', { name: 'Turn alerts back on' })
    await userEvent.dblClick(turnOn)
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(1))
    expect(api.change).toHaveBeenCalledWith(1, { version: 3, action: 'alerts_on' })
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Turn alerts back on' })).not.toBeInTheDocument()
    )
  })
})

describe('what the service says of a run', () => {
  it('names waiting, not checked, a check that did not finish, and alerts, in words', () => {
    render(
      <RunFacts
        run={runRow(1, {
          missing: 2,
          not_checked: 3,
          status: 'failed',
          alerts: 'Told: 2 new',
        })}
      />
    )
    expect(screen.getByText('2 waiting to leave')).toBeInTheDocument()
    expect(screen.getByText('3 not checked')).toBeInTheDocument()
    expect(screen.getByText('This check did not finish')).toBeInTheDocument()
    expect(screen.getByText('Told: 2 new')).toBeInTheDocument()
  })

  it('shows nothing for a plain finished run', () => {
    const { container } = render(<RunFacts run={runRow(1, { status: 'done' })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says why a past run has no values', async () => {
    api.run.mockResolvedValue({
      screen_id: 1,
      run: runRow(5),
      rows: [],
      not_checked: { count: 0, reasons: [] },
      values_note: 'Values are kept for 7 days; the list for 100 days.',
    })
    const client = createQueryClient()
    render(
      <QueryClientProvider client={client}>
        <RunSheet runId={5} onClose={() => {}} />
      </QueryClientProvider>
    )
    expect(await screen.findByText(/Values are kept for 7 days/)).toBeInTheDocument()
  })
})

describe('a preview', () => {
  it('says what history the screen needs and that warming conditions judge later', () => {
    render(
      <PreviewBody
        preview={{
          label: 'Preview on the 10:15 to 10:30 candle, not saved',
          count: 0,
          first: [],
          not_checked: {
            count: 4,
            reasons: ['Not checked: warming up (3 of 20 candles so far)'],
          },
          needs: { D: 20 },
        }}
      />
    )
    expect(screen.getByText(/Needs 20 candles of day/)).toBeInTheDocument()
    expect(screen.getByText(/still collecting candles/)).toBeInTheDocument()
  })
})
