/**
 * The Screener page (M9.3) in jsdom: the bridge is a stand-in answering as the contract says and
 * the shared connection is fired by hand. Pinned: an icon and a word on every status, the
 * server's words as they come, the engine line never claiming real-time while this screen is not
 * in the owner's room, Back closing the sheet first and the screen next, and Pause and Delete
 * sent once with their outcomes, including "Not confirmed" and the version-conflict words.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  type InitialEntry,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router'
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
import { NO_ANSWER_HOLD_MS, resetScreenActionsForTests } from '@/sudarshan/screener/actions'
import { ScreenerError } from '@/sudarshan/screener/api'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { WORDS } from '@/sudarshan/screener/types'
import { fakeSocket, screenerState, screenRow } from '@/test/screener'
import Screener from './Screener'

vi.setConfig({ testTimeout: 15_000 })

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}
const history = { go: (_delta: number) => {} }
function Nav() {
  const navigate = useNavigate()
  history.go = (delta) => navigate(delta)
  return null
}

function renderPage(entries: InitialEntry[] = ['/elsewhere', '/screener']) {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Nav />
        <Routes>
          <Route path="/screener" element={<Screener />} />
          <Route path="/elsewhere" element={<p>Another page</p>} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}
const where = () => screen.getByTestId('where').textContent
const back = () => act(() => history.go(-1))

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
}

/** The owner's room joined: the page hears pushes. */
async function joined(room: ReturnType<typeof fakeSocket>) {
  await settle()
  const join = room.emits.find((each) => each.event === 'sudarshan_screener_join')
  expect(join).toBeDefined()
  act(() => join?.ack(null, { ok: true }))
  await settle()
}

beforeEach(() => {
  sockets.current = null
  resetScreenActionsForTests()
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
})
afterEach(() => vi.useRealTimers())

describe('the Screener page', () => {
  it('lists the screens, active first, each with an icon and a word for its status', async () => {
    api.state.mockResolvedValue(
      screenerState([
        screenRow({
          id: 1,
          name: 'Stopped one',
          status: 'stopped',
          status_reason: 'Its list was deleted.',
        }),
        screenRow({ id: 2, name: 'Paused one', status: 'paused', next_check: null }),
        screenRow({ id: 3, name: 'Active one' }),
      ])
    )
    renderPage()
    const first = await screen.findByRole('button', { name: /Active one/ })
    const cards = screen.getAllByRole('listitem')
    expect(
      cards.map((each) => within(each).getByRole('button').getAttribute('data-screen'))
    ).toEqual(['3', '2', '1'])
    for (const [status, word] of [
      ['active', 'Active'],
      ['paused', 'Paused'],
      ['stopped', 'Stopped'],
    ]) {
      const chip = document.querySelector(`[data-status="${status}"]`) as HTMLElement
      expect(chip.textContent).toBe(word)
      expect(chip.querySelector('svg')).not.toBeNull()
    }
    expect(screen.getByText('Its list was deleted.')).toBeTruthy()
    expect(within(first).getByText('7 match · 3 new today')).toBeTruthy()
    expect(within(first).getByText('next check 10:45')).toBeTruthy()
  })

  it('says what is wrong in the owner words when the screens cannot be read', async () => {
    api.state.mockRejectedValue(
      new ScreenerError({ code: 'unanswered', message: 'x', field: null })
    )
    renderPage()
    expect(await screen.findByText(WORDS.not_answering)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Try again/ })).toBeTruthy()
  })

  it('shows the switched-off words when the server has the screener off', async () => {
    api.state.mockResolvedValue(
      screenerState([], {
        enabled: false,
        engine: { state: 'switched_off', line: 'Switched off on the server', real_time: false },
      })
    )
    renderPage()
    expect(await screen.findByText(WORDS.switched_off)).toBeTruthy()
  })

  it('never says real-time while this screen is not in the owner room', async () => {
    api.state.mockResolvedValue(screenerState())
    const room = fakeSocket()
    sockets.current = room.socket
    renderPage()
    await waitFor(() => expect(document.querySelector('[data-heard="no"]')).not.toBeNull())
    expect(screen.queryByText(/Real-time/)).toBeNull()
    await joined(room)
    await waitFor(() => expect(document.querySelector('[data-heard="yes"]')).not.toBeNull())
    expect(screen.getByText('Real-time · next check 10:45')).toBeTruthy()
    // The connection drops: the line leaves real-time at once.
    act(() => room.fire('disconnect'))
    expect(screen.queryByText(/Real-time/)).toBeNull()
  })

  it('moves a card on a pushed run, and reads the state again for a screen it does not hold', async () => {
    api.state.mockResolvedValue(screenerState())
    const room = fakeSocket()
    sockets.current = room.socket
    renderPage()
    await joined(room)
    act(() =>
      room.fire('sudarshan_screener_run', {
        screen_id: 1,
        run_id: 11,
        run_key: 'k',
        at: 1,
        candle: '10:15 to 10:30',
        counts: { matches: 9, entered: 2, exited: 0, missing: 0, not_checked: 0 },
        entered: [],
        exited: [],
        marks: [],
      })
    )
    expect(await screen.findByText('9 match · 5 new today')).toBeTruthy()
    const reads = api.state.mock.calls.length
    act(() =>
      room.fire('sudarshan_screener_run', {
        screen_id: 99,
        run_id: 12,
        run_key: 'j',
        at: 1,
        candle: '',
        counts: { matches: 1, entered: 1, exited: 0, missing: 0, not_checked: 0 },
        entered: [],
        exited: [],
        marks: [],
      })
    )
    await settle()
    expect(api.state.mock.calls.length).toBeGreaterThan(reads)
  })

  it('catches up on the runs after a reconnect', async () => {
    api.state.mockResolvedValue(screenerState())
    api.runsSince.mockResolvedValue({ runs: [{ id: 11 }], more: false })
    const room = fakeSocket()
    sockets.current = room.socket
    renderPage()
    await joined(room)
    expect(api.runsSince).toHaveBeenCalledWith(10)
  })

  it('opens a screen, and the phone Back closes the sheet first, then the screen', async () => {
    api.state.mockResolvedValue(screenerState())
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /15-min EMA cross/ }))
    expect(where()).toBe('/screener?screen=1')
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(where()).toBe('/screener?screen=1&sheet=delete')
    expect(screen.getByText('Delete this screen?')).toBeTruthy()
    back()
    await waitFor(() => expect(where()).toBe('/screener?screen=1'))
    expect(screen.queryByText('Delete this screen?')).toBeNull()
    back()
    await waitFor(() => expect(where()).toBe('/screener'))
    back()
    await waitFor(() => expect(where()).toBe('/elsewhere'))
  })

  it('closes a screen link the page did not open without leaving the app', async () => {
    api.state.mockResolvedValue(screenerState())
    renderPage(['/screener?screen=1'])
    await userEvent.click(await screen.findByRole('button', { name: 'Back to screens' }))
    expect(where()).toBe('/screener')
  })

  it('says a screen deleted on another screen is gone, once, and closes it', async () => {
    api.state.mockResolvedValue(screenerState([screenRow({ id: 2 })]))
    renderPage(['/screener?screen=1'])
    await waitFor(() => expect(where()).toBe('/screener'))
    expect(toasts.info).toHaveBeenCalledWith(WORDS.gone)
  })

  it('sends a pause once however often it is tapped, and the new row takes the old place', async () => {
    api.state.mockResolvedValue(screenerState())
    let answer: (row: unknown) => void = () => {}
    api.change.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    renderPage(['/screener?screen=1'])
    const pause = await screen.findByRole('button', { name: 'Pause' })
    await userEvent.click(pause)
    await userEvent.click(screen.getByRole('button', { name: 'Pausing' }))
    expect(api.change).toHaveBeenCalledTimes(1)
    expect(api.change).toHaveBeenCalledWith(1, { version: 1, action: 'pause' })
    await act(async () => answer(screenRow({ version: 2, status: 'paused' })))
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeTruthy()
  })

  it('says Not confirmed when no answer came, holds the buttons, then lets go', async () => {
    api.state.mockResolvedValue(screenerState())
    api.change.mockRejectedValue(
      new ScreenerError({ code: 'unanswered', message: 'x', field: null })
    )
    renderPage(['/screener?screen=1'])
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Pause' }))
    await settle()
    expect(toasts.warning).toHaveBeenCalledWith(expect.stringContaining('Not confirmed'))
    expect(
      (screen.getByRole('button', { name: 'Not confirmed' }) as HTMLButtonElement).disabled
    ).toBe(true)
    await act(async () => {
      vi.advanceTimersByTime(NO_ANSWER_HOLD_MS + 10)
    })
    expect((screen.getByRole('button', { name: 'Pause' }) as HTMLButtonElement).disabled).toBe(
      false
    )
  })

  it('shows the version-conflict words and reads the screens again', async () => {
    api.state.mockResolvedValue(screenerState())
    api.change.mockRejectedValue(
      new ScreenerError({ code: 'conflict', message: 'raw', field: null })
    )
    renderPage(['/screener?screen=1'])
    await userEvent.click(await screen.findByRole('button', { name: 'Pause' }))
    await settle()
    expect(toasts.error).toHaveBeenCalledWith(WORDS.conflict)
    expect(api.state.mock.calls.length).toBeGreaterThan(1)
  })

  it('shows the session-ended words as the server side wrote them', async () => {
    api.state.mockResolvedValue(screenerState())
    api.change.mockRejectedValue(
      new ScreenerError({ code: 'unauthorized', message: WORDS.session_ended, field: null })
    )
    renderPage(['/screener?screen=1'])
    await userEvent.click(await screen.findByRole('button', { name: 'Pause' }))
    await settle()
    expect(toasts.error).toHaveBeenCalledWith(WORDS.session_ended)
  })

  it('asks before deleting, offers to remove what it added (No by default), and sends once', async () => {
    api.state.mockResolvedValue(screenerState())
    api.remove.mockResolvedValue({ deleted: true })
    renderPage(['/screener?screen=1&sheet=delete'])
    const box = await screen.findByRole('checkbox', {
      name: "Also remove the 7 instruments it added to 'Screener: 15-min EMA cross'?",
    })
    expect((box as HTMLInputElement).checked).toBe(false)
    await userEvent.click(box)
    const button = screen.getByRole('button', { name: 'Delete' })
    await userEvent.dblClick(button)
    await settle()
    expect(api.remove).toHaveBeenCalledTimes(1)
    expect(api.remove).toHaveBeenCalledWith(1, true)
    expect(toasts.success).toHaveBeenCalledWith('Screen deleted. Its history stays.')
  })
})
