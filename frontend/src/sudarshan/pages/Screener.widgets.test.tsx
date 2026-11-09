/**
 * Adding, arranging and removing dashboard widgets, and a widget's settings (M9.16, lane C2), in
 * jsdom: the Add sheet from a screen and from a ready-made one, the limit refusal in the
 * service's words, the watchlist notice at OpenAlgo's 50, the whole order sent once, removal that
 * asks first and sends once, each setting as one write against the dashboard's version, a moved
 * layout reading the dashboard again, and half a second of ignored taps on each sheet.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { type InitialEntry, MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  runsSince: vi.fn(),
  dashboard: vi.fn(),
  widget: vi.fn(),
  presets: vi.fn(),
  universes: vi.fn(),
  create: vi.fn(),
  change: vi.fn(),
  widgetAdd: vi.fn(),
  widgetUpdate: vi.fn(),
  widgetRemove: vi.fn(),
  widgetOrder: vi.fn(),
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
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: null }),
}))
const lists = vi.hoisted(() => ({ now: [] as Array<{ id: number; name: string }> }))
vi.mock('@/sudarshan/watchlist/useWatchlists', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/watchlist/useWatchlists')>()),
  readLists: async () => lists.now.map((list, position) => ({ ...list, position, items: [] })),
}))

import { createQueryClient } from '@/app/providers'
import { LISTS_USED_UP } from '@/sudarshan/screener/AddWidgetSheet'
import { ScreenerError } from '@/sudarshan/screener/api'
import { DASH_CONFLICT } from '@/sudarshan/screener/dashboardCache'
import { DASH_GONE, DASH_REMOVED, pausedAddWords } from '@/sudarshan/screener/dashboardWrites'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import type { PresetCard, ScreenSpec } from '@/sudarshan/screener/types'
import { clauseSentence } from '@/sudarshan/screener/words'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { dashboard, SPEC, screenerState, screenRow, widgetCard } from '@/test/screener'
import Screener from './Screener'

vi.setConfig({ testTimeout: 20_000 })

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}
function renderPage(entry = '/screener') {
  const client = createQueryClient()
  const initial: InitialEntry[] = [entry]
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initial}>
        <Routes>
          <Route path="/screener" element={<Screener />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
const where = () => screen.getByTestId('where').textContent
const shown = () =>
  Array.from(document.querySelectorAll('[data-widget]')).map((card) =>
    card.getAttribute('data-widget')
  )
const dialog = () => screen.getByRole('dialog')

const DAY_SPEC: ScreenSpec = {
  ...SPEC,
  name: 'Opened at the low',
  run: { on: 'after_close' },
  where: {
    all: [
      {
        id: 'c1',
        on: true,
        left: { field: 'open', tf: 'D' },
        op: 'eq',
        right: { field: 'low', tf: 'D' },
      },
    ],
  },
  target: {
    list_id: null,
    create_name: 'Screener: Opened at the low',
    grace: { mode: 'runs', n: 3 },
    max_adds_per_run: 10,
  },
}
const PRESETS: PresetCard[] = [
  {
    key: 'low',
    name: 'Opened at the low',
    about: 'Open equals low.',
    usable: true,
    where: 'F&O stocks',
    spec: DAY_SPEC,
    reason: null,
  },
  {
    key: 'later',
    name: 'Two-day reversal',
    about: 'Needs history.',
    usable: false,
    where: null,
    spec: null,
    reason: 'Waits for the history download.',
  },
]
const LIMIT =
  'Up to 12 screens can check during the day at once. Pause one, or make this one run after the close.'

beforeEach(() => {
  OPEN_GUARD.ms = 0
  resetScreenerPushForTests()
  for (const fn of [...Object.values(api), ...Object.values(toasts)]) fn.mockReset()
  lists.now = [{ id: 4, name: 'Swing' }]
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.presets.mockResolvedValue({ presets: PRESETS })
  api.state.mockResolvedValue(
    screenerState([
      screenRow({ id: 1, name: 'One' }),
      screenRow({ id: 2, name: 'Two' }),
      screenRow({ id: 3, name: 'Three', status: 'paused' }),
    ])
  )
  api.dashboard.mockResolvedValue(
    dashboard([widgetCard({ screen_id: 1 }), widgetCard({ screen_id: 2, name: 'Two' })], {
      version: 2,
      suggested: false,
    })
  )
})
afterEach(() => {
  OPEN_GUARD.ms = 0
})

describe('adding a widget', () => {
  it('adds one of your screens as a table: the conditions first, then one write', async () => {
    api.widgetAdd.mockResolvedValue({
      version: 3,
      kind: 'table',
      screen: screenRow({ id: 3, name: 'Three' }),
      notice: null,
    })
    renderPage('/screener?sheet=add')
    // The two screens already shown for a table are not offered again.
    expect(screen.queryByRole('button', { name: /^One/ })).toBeNull()
    await userEvent.click(await screen.findByRole('button', { name: /^Three/ }))
    for (const clause of SPEC.where.all) {
      if ('op' in clause) expect(within(dialog()).getByText(clauseSentence(clause))).toBeTruthy()
    }
    await userEvent.dblClick(screen.getByRole('button', { name: 'Add to dashboard' }))
    await waitFor(() => expect(where()).toBe('/screener'))
    expect(api.widgetAdd).toHaveBeenCalledTimes(1)
    expect(api.widgetAdd).toHaveBeenCalledWith({
      version: 2,
      kind: 'table',
      rows: 10,
      start: true,
      screen_id: 3,
    })
    expect(toasts.success).toHaveBeenCalledWith('Added to the dashboard.')
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
  })

  it('offers a screen again for the other kind, and sends no rows for a count', async () => {
    api.widgetAdd.mockResolvedValue({
      version: 3,
      kind: 'count',
      screen: screenRow({ id: 1 }),
      notice: null,
    })
    renderPage('/screener?sheet=add')
    await userEvent.click(await screen.findByRole('button', { name: 'Count' }))
    await userEvent.click(screen.getByRole('button', { name: /^One/ }))
    expect(screen.queryByRole('button', { name: '5 rows' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Add to dashboard' }))
    await waitFor(() => expect(api.widgetAdd).toHaveBeenCalled())
    expect(api.widgetAdd).toHaveBeenCalledWith({
      version: 2,
      kind: 'count',
      start: false,
      screen_id: 1,
    })
  })

  it('makes the screen and the widget in one step from a ready-made screen, and says why another cannot be chosen', async () => {
    api.widgetAdd.mockResolvedValue({
      version: 3,
      kind: 'table',
      screen: screenRow({ id: 9, name: 'Opened at the low' }),
      notice: 'Added Paused. Read it, then resume.',
    })
    renderPage('/screener?sheet=add')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('tab', { name: 'Library' })
    )
    expect(await screen.findByText(/Two-day reversal: Waits for the history download/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Two-day reversal/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /^Opened at the low/ }))
    await userEvent.click(screen.getByRole('button', { name: '5 rows' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add to dashboard' }))
    await waitFor(() => expect(where()).toBe('/screener'))
    expect(api.create).not.toHaveBeenCalled()
    expect(api.widgetAdd).toHaveBeenCalledWith({
      version: 2,
      kind: 'table',
      rows: 5,
      start: true,
      preset: 'low',
    })
    expect(toasts.success).toHaveBeenCalledWith('Added Paused. Read it, then resume.')
  })

  it('offers when a day-only screen checks, sends only a change, and shows the limit sentence in place', async () => {
    api.widgetAdd.mockRejectedValue(
      new ScreenerError({ code: 'invalid', message: LIMIT, field: null })
    )
    renderPage('/screener?sheet=add')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('tab', { name: 'Library' })
    )
    await userEvent.click(await screen.findByRole('button', { name: /^Opened at the low/ }))
    // The preset runs after the close: that is the chosen one, and a screen on SPEC is not day-only.
    expect(
      screen.getByRole('button', { name: 'After the close (16:05)', pressed: true })
    ).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', {
        name: 'During the day: the forming day candle at each 15-minute close',
      })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Add to dashboard' }))
    expect(await within(dialog()).findByText(LIMIT)).toBeTruthy()
    expect(where()).toBe('/screener?sheet=add')
    expect(api.widgetAdd.mock.calls[0][0]).toMatchObject({ when: 'during_day', preset: 'low' })
  })

  it('adds Paused, with the reason, when twelve screens already check through the day', async () => {
    api.state.mockResolvedValue(
      screenerState([screenRow({ id: 3, name: 'Three', status: 'paused' })], {
        limits: { active: 12, active_used: 12 },
      })
    )
    api.dashboard.mockResolvedValue(dashboard([], { version: 1, suggested: false }))
    api.widgetAdd.mockResolvedValue({
      version: 2,
      kind: 'table',
      screen: screenRow({ id: 3, status: 'paused' }),
      notice: null,
    })
    renderPage('/screener?sheet=add')
    await userEvent.click(await screen.findByRole('button', { name: /^Three/ }))
    const start = screen.getByRole('button', { name: /Start checking after adding/ })
    expect(start).toBeDisabled()
    expect(start.textContent).toContain('off')
    expect(screen.getByText(pausedAddWords(12, 12))).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Add to dashboard' }))
    await waitFor(() => expect(api.widgetAdd).toHaveBeenCalled())
    expect(api.widgetAdd.mock.calls[0][0].start).toBe(false)
  })

  it('says all 50 watchlists are used and asks for one of the lists before it adds', async () => {
    lists.now = Array.from({ length: 50 }, (_, index) => ({
      id: index + 1,
      name: `List ${index + 1}`,
    }))
    api.create.mockResolvedValue(screenRow({ id: 9, name: 'Opened at the low', status: 'paused' }))
    api.widgetAdd.mockResolvedValue({
      version: 3,
      kind: 'table',
      screen: screenRow({ id: 9, name: 'Opened at the low' }),
      notice: null,
    })
    renderPage('/screener?sheet=add')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('tab', { name: 'Library' })
    )
    await userEvent.click(await screen.findByRole('button', { name: /^Opened at the low/ }))
    expect(await screen.findByText(LISTS_USED_UP)).toBeTruthy()
    const add = screen.getByRole('button', { name: 'Add to dashboard' })
    expect(add).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText("List for this screen's matches"), '7')
    expect(add).toBeEnabled()
    await userEvent.click(add)
    await waitFor(() => expect(api.widgetAdd).toHaveBeenCalled())
    expect(api.create.mock.calls[0][0].spec.target).toMatchObject({ list_id: 7, create_name: null })
    expect(api.widgetAdd.mock.calls[0][0]).toMatchObject({ screen_id: 9 })
    expect(api.widgetAdd.mock.calls[0][0].preset).toBeUndefined()
  })

  it('shows no watchlist notice while fewer than 50 are in use', async () => {
    renderPage('/screener?sheet=add')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('tab', { name: 'Library' })
    )
    await userEvent.click(await screen.findByRole('button', { name: /^Opened at the low/ }))
    await waitFor(() => expect(lists.now.length).toBe(1))
    expect(screen.queryByText(LISTS_USED_UP)).toBeNull()
  })
})

describe('arranging and removing', () => {
  it('sends the whole order once, by [screen, kind], against the version', async () => {
    api.widgetOrder.mockResolvedValue({ version: 3 })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Widget options for One' }))
    expect(screen.getByRole('button', { name: 'Move up' })).toBeDisabled()
    await userEvent.dblClick(screen.getByRole('button', { name: 'Move down' }))
    await waitFor(() => expect(where()).toBe('/screener'))
    expect(api.widgetOrder).toHaveBeenCalledTimes(1)
    expect(api.widgetOrder).toHaveBeenCalledWith({
      version: 2,
      keys: [
        [2, 'table'],
        [1, 'table'],
      ],
    })
    expect(shown()).toEqual(['2:table', '1:table'])
    // The next move names the version the last write answered.
    await userEvent.click(screen.getByRole('button', { name: 'Widget options for One' }))
    expect(screen.getByRole('button', { name: 'Move down' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Move up' }))
    await waitFor(() => expect(api.widgetOrder).toHaveBeenCalledTimes(2))
    expect(api.widgetOrder.mock.calls[1][0].version).toBe(3)
  })

  it('asks before it removes, and sends once when confirmed', async () => {
    api.widgetRemove.mockResolvedValue({ version: 3 })
    renderPage()
    const open = async () =>
      userEvent.click(await screen.findByRole('button', { name: 'Widget options for Two' }))
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Remove from dashboard' }))
    expect(await screen.findByText('Remove from the dashboard?')).toBeTruthy()
    expect(api.widgetRemove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(api.widgetRemove).not.toHaveBeenCalled()
    expect(shown()).toEqual(['1:table', '2:table'])
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Remove from dashboard' }))
    await userEvent.dblClick(await screen.findByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(shown()).toEqual(['1:table']))
    expect(api.widgetRemove).toHaveBeenCalledTimes(1)
    expect(api.widgetRemove).toHaveBeenCalledWith(2, 'table', 2)
    expect(toasts.success).toHaveBeenCalledWith(DASH_REMOVED)
  })

  it('reads the dashboard again and says the layout moved on a version conflict, saving nothing', async () => {
    api.widgetOrder.mockRejectedValue(
      new ScreenerError({ code: 'conflict', message: 'x', field: null })
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Widget options for One' }))
    await userEvent.click(screen.getByRole('button', { name: 'Move down' }))
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(DASH_CONFLICT))
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
    expect(shown()).toEqual(['1:table', '2:table'])
  })

  it('never says the screen is gone for a widget whose screen is still there', async () => {
    api.widgetRemove.mockRejectedValue(
      new ScreenerError({ code: 'gone', message: 'x', field: null })
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Widget options for Two' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove from dashboard' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(DASH_CONFLICT))
    expect(toasts.error).not.toHaveBeenCalledWith(DASH_GONE)
  })

  it('says the screen is gone only when the page no longer holds the screen', async () => {
    api.state.mockResolvedValue(screenerState([screenRow({ id: 1, name: 'One' })]))
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 1 }), widgetCard({ screen_id: 9, name: 'Nine' })], {
        version: 2,
        suggested: false,
      })
    )
    api.widgetRemove.mockRejectedValue(
      new ScreenerError({ code: 'gone', message: 'x', field: null })
    )
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Widget options for Nine' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove from dashboard' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(DASH_GONE))
  })

  it('works on a suggested layout, addressed by screen and kind, at version 0', async () => {
    api.dashboard.mockResolvedValue(dashboard([widgetCard({ screen_id: 1 })]))
    api.widgetRemove.mockResolvedValue({ version: 1 })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Widget options for One' }))
    await userEvent.click(screen.getByRole('button', { name: 'Open screen' }))
    await waitFor(() => expect(where()).toBe('/screener?screen=1'))
  })
})

describe("a widget's settings", () => {
  const LONG = 'Opening-range follow-through, up, on the F&O stocks with a rising 15-minute volume'
  beforeEach(() => {
    api.state.mockResolvedValue(
      screenerState([screenRow({ id: 1, name: LONG }), screenRow({ id: 2, name: 'Two' })])
    )
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 1, name: LONG }), widgetCard({ screen_id: 2 })], {
        version: 2,
        suggested: false,
      })
    )
    api.widgetUpdate.mockResolvedValue({ version: 3 })
  })
  const update = (set: object) => ({ version: 2, kind: 'table', set })

  it('opens from the options menu and shows the full name', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: `Widget options for ${LONG}` }))
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(where()).toBe('/screener?sheet=widget&s=1&k=table')
    expect(within(dialog()).getByText(LONG)).toBeTruthy()
  })

  it('sends the rows, the sort and the two values, one write each, against the version', async () => {
    const cards = [widgetCard({ screen_id: 1, name: LONG }), widgetCard({ screen_id: 2 })]
    api.dashboard.mockReset()
    api.dashboard.mockResolvedValueOnce(dashboard(cards, { version: 2, suggested: false }))
    api.dashboard.mockResolvedValue(dashboard(cards, { version: 3, suggested: false }))
    renderPage('/screener?sheet=widget&s=1&k=table')
    await userEvent.click(await screen.findByRole('button', { name: '5 rows' }))
    await waitFor(() => expect(api.widgetUpdate).toHaveBeenCalledTimes(1))
    expect(api.widgetUpdate).toHaveBeenLastCalledWith(1, update({ rows: 5 }))
    await userEvent.click(screen.getByRole('button', { name: 'Change at the run' }))
    await waitFor(() => expect(api.widgetUpdate).toHaveBeenCalledTimes(2))
    expect(api.widgetUpdate.mock.calls[1][1]).toMatchObject({
      version: 3,
      set: { sort_by: { by: 'change_pct', order: 'desc' } },
    })
    const [first] = SPEC.where.all.filter((node) => 'op' in node)
    await userEvent.click(screen.getByRole('button', { name: clauseSentence(first as never) }))
    await waitFor(() => expect(api.widgetUpdate).toHaveBeenCalledTimes(3))
    expect(api.widgetUpdate.mock.calls[2][1].set).toEqual({ value_clauses: ['c1'] })
  })

  it('changes the kind and closes, and an answered conflict reads the dashboard again', async () => {
    renderPage('/screener?sheet=widget&s=1&k=table')
    await userEvent.click(await screen.findByRole('button', { name: 'Count' }))
    await waitFor(() => expect(where()).toBe('/screener'))
    expect(api.widgetUpdate).toHaveBeenCalledWith(1, update({ kind: 'count' }))
  })

  it('shows the layout-moved sentence in place on a version conflict and reads the dashboard again', async () => {
    api.widgetUpdate.mockRejectedValue(
      new ScreenerError({ code: 'conflict', message: 'x', field: null })
    )
    renderPage('/screener?sheet=widget&s=1&k=table')
    await userEvent.click(await screen.findByRole('button', { name: '20 rows' }))
    expect(await within(dialog()).findByText(DASH_CONFLICT)).toBeTruthy()
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2))
  })

  it('offers when it checks for a day-only screen and writes it as a normal edit of the screen', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 1, day_only: true, name: LONG })], {
        version: 2,
        suggested: false,
      })
    )
    api.change.mockResolvedValue(screenRow({ id: 1, version: 2, name: LONG }))
    renderPage('/screener?sheet=widget&s=1&k=table')
    await userEvent.click(await screen.findByRole('button', { name: 'After the close (16:05)' }))
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(1))
    expect(api.change.mock.calls[0][0]).toBe(1)
    expect(api.change.mock.calls[0][1]).toMatchObject({
      version: 1,
      action: 'edit',
      spec: { run: { on: 'after_close' } },
    })
    expect(api.widgetUpdate).not.toHaveBeenCalled()
  })

  it('shows the limit sentence in place when the service refuses the choice', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 1, day_only: true, name: LONG })], {
        version: 2,
        suggested: false,
      })
    )
    api.state.mockResolvedValue(
      screenerState([
        screenRow({ id: 1, name: LONG, spec: { ...SPEC, run: { on: 'after_close' } } }),
      ])
    )
    api.change.mockRejectedValue(
      new ScreenerError({ code: 'invalid', message: LIMIT, field: null })
    )
    renderPage('/screener?sheet=widget&s=1&k=table')
    await userEvent.click(
      await screen.findByRole('button', {
        name: 'During the day: the forming day candle at each 15-minute close',
      })
    )
    expect(await within(dialog()).findByText(LIMIT)).toBeTruthy()
  })

  it('shows no when-it-checks choice for a screen that reads minute candles', async () => {
    renderPage('/screener?sheet=widget&s=2&k=table')
    await screen.findByRole('button', { name: '5 rows' })
    expect(screen.queryByText('When it checks')).toBeNull()
  })
})

describe('the half second after a sheet opens', () => {
  beforeEach(() => {
    OPEN_GUARD.ms = 500
    api.widgetOrder.mockResolvedValue({ version: 3 })
    api.widgetUpdate.mockResolvedValue({ version: 3 })
    api.widgetAdd.mockResolvedValue({
      version: 3,
      kind: 'table',
      screen: screenRow({ id: 3 }),
      notice: null,
    })
  })
  const settled = () =>
    waitFor(() => expect(dialog().hasAttribute('data-settling')).toBe(false), { timeout: 2000 })

  it('ignores a tap on the options menu, then takes it', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Widget options for One' }))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: 'Move down' }))
    expect(api.widgetOrder).not.toHaveBeenCalled()
    await settled()
    fireEvent.click(screen.getByRole('button', { name: 'Move down' }))
    await waitFor(() => expect(api.widgetOrder).toHaveBeenCalledTimes(1))
  })

  it('ignores a tap on the settings sheet, then takes it', async () => {
    renderPage('/screener?sheet=widget&s=1&k=table')
    const rows = await screen.findByRole('button', { name: '5 rows' })
    fireEvent.click(rows)
    expect(api.widgetUpdate).not.toHaveBeenCalled()
    await settled()
    fireEvent.click(screen.getByRole('button', { name: '5 rows' }))
    await waitFor(() => expect(api.widgetUpdate).toHaveBeenCalledTimes(1))
  })

  it('ignores a tap on the Add sheet, then takes it', async () => {
    renderPage('/screener?sheet=add')
    const pick = await screen.findByRole('button', { name: /^Three/ })
    fireEvent.click(pick)
    expect(screen.queryByRole('button', { name: /Start checking after adding/ })).toBeNull()
    await settled()
    fireEvent.click(screen.getByRole('button', { name: /^Three/ }))
    expect(await screen.findByRole('button', { name: /Start checking after adding/ })).toBeTruthy()
  })

  it('holds the removal confirmation for half a second too', async () => {
    api.widgetRemove.mockResolvedValue({ version: 3 })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Widget options for Two' }))
    await settled()
    fireEvent.click(screen.getByRole('button', { name: 'Remove from dashboard' }))
    const remove = await screen.findByRole('button', { name: 'Remove' })
    fireEvent.click(remove)
    expect(api.widgetRemove).not.toHaveBeenCalled()
    await waitFor(() => expect(remove).toBeEnabled(), { timeout: 2000 })
    fireEvent.click(remove)
    await waitFor(() => expect(api.widgetRemove).toHaveBeenCalledTimes(1))
  })
})
