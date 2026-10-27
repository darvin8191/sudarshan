/**
 * The Alerts page (M10.3), end to end in jsdom: the bridge is a stand-in answering as the
 * contract says, the market-data manager is the shared harness (so subscriptions are real), and
 * frames run by hand. Pinned: the server's words as they come, an icon and a word for every
 * status and line, the tabs, each card's live price with the M3.7 freshness mark (never
 * "Real-time" once stale, the auction line in an auction), the 100-instrument cap, Back closing
 * the sheet first, and Pause and Delete sent once with the contract's outcomes.
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

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  state: vi.fn(),
  eventsSince: vi.fn(),
  create: vi.fn(),
  change: vi.fn(),
  remove: vi.fn(),
}))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))

const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ toast: toasts, showToast: toasts }))

import { type AlertRow, AlertsError, type LineCode } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { NO_ANSWER_HOLD_MS, resetAlertActionsForTests } from '@/sudarshan/alerts/actions'
import { LineText } from '@/sudarshan/alerts/lines'
import { resetMarketAlertsForTests } from '@/sudarshan/alerts/marketAlerts'
import { STALE_MS } from '@/sudarshan/dashboard/live'
import { fakeClock, frames } from '@/test/frames'
import { alertRow, alertsState, NOW } from '@/test/marketAlerts'
import { feed } from '@/test/marketDataHarness'
import MarketAlerts, { LIVE_CAP } from './MarketAlerts'

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

function renderPage(entries: InitialEntry[] = ['/elsewhere', '/market-alerts']) {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Nav />
        <Routes>
          <Route path="/market-alerts" element={<MarketAlerts />} />
          <Route path="/elsewhere" element={<p>Another page</p>} />
          <Route path="*" element={null} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}

const where = () => screen.getByTestId('where').textContent
const back = () => act(() => history.go(-1))
const card = (id: number) => document.querySelector(`[data-alert="${id}"]`) as HTMLElement

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
  act(() => frames.flush())
}

function tick(symbol: string, exchange: string, data: Record<string, unknown>) {
  act(() => {
    feed.push(symbol, exchange, data)
    frames.flush()
  })
}

const refused = (code: string, message: string) => new AlertsError({ code, message, field: null })

beforeEach(() => {
  fakeClock()
  vi.setSystemTime(NOW)
  feed.reset()
  frames.install()
  resetMarketAlertsForTests()
  resetAlertActionsForTests()
  for (const fn of [...Object.values(api), ...Object.values(toasts)]) fn.mockReset()
  api.state.mockResolvedValue(alertsState())
  useAuthStore.setState({ apiKey: 'key' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the Alerts page', () => {
  it('says what the alert service says under the title, as it says it', async () => {
    renderPage()
    await settle()

    expect(screen.getByRole('heading', { name: 'Alerts' })).toBeInTheDocument()
    const line = screen.getByText('Watching · Real-time')
    expect(line.closest('[data-line="ok"]')?.querySelector('svg')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'New' })).toBeEnabled()
  })

  it('draws every line with an icon and the server’s words, whatever its code', () => {
    const codes: LineCode[] = [
      'ok',
      'closing_auction',
      'feed_down',
      'not_open_yet',
      'closed_by_exchange',
      'outside_hours',
      'closed_today',
      'waiting',
      'note',
      'starting',
      'off',
    ]
    const { container } = render(
      <div>
        {codes.map((code) => (
          <LineText key={code} line={{ code, line: `words for ${code}`, real_time: false }} />
        ))}
      </div>
    )
    for (const code of codes) {
      const line = container.querySelector(`[data-line="${code}"]`)
      expect(line?.querySelector('svg')).not.toBeNull()
      expect(line?.textContent).toBe(`words for ${code}`)
    }
  })

  it('shows every status with its icon and its word, and why one stopped', async () => {
    api.state.mockResolvedValue(
      alertsState({
        alerts: [
          alertRow({ id: 1, status: 'active' }),
          alertRow({
            id: 2,
            status: 'paused',
            status_reason: 'Fired 50 times today; paused so Telegram is not flooded',
          }),
          alertRow({ id: 3, status: 'stopped', status_reason: 'Fired once at 10:42 on 21 Oct' }),
        ],
      })
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()

    for (const [id, word] of [
      [1, 'Active'],
      [2, 'Paused'],
    ] as const) {
      const chip = card(id).querySelector('[data-status]')
      expect(chip?.textContent).toBe(word)
      expect(chip?.querySelector('svg')).not.toBeNull()
    }
    expect(
      within(card(2)).getByText('Fired 50 times today; paused so Telegram is not flooded')
    ).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /Stopped/ }))
    const chip = card(3).querySelector('[data-status]')
    expect(chip?.textContent).toBe('Stopped')
    expect(chip?.querySelector('svg')).not.toBeNull()
    expect(within(card(3)).getByText('Fired once at 10:42 on 21 Oct')).toBeInTheDocument()
  })

  it('lists Active and Paused under Active, Stopped under Stopped, and moves between tabs without adding to Back', async () => {
    api.state.mockResolvedValue(
      alertsState({
        alerts: [
          alertRow({ id: 1, status: 'paused' }),
          alertRow({ id: 2, status: 'active' }),
          alertRow({ id: 3, status: 'stopped' }),
        ],
      })
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()

    expect(screen.getByRole('tab', { name: 'Active (2)' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Stopped (1)' })).toBeInTheDocument()
    const order = [...document.querySelectorAll('[data-alert]')].map((each) =>
      each.getAttribute('data-alert')
    )
    expect(order).toEqual(['2', '1'])

    await user.click(screen.getByRole('tab', { name: 'Stopped (1)' }))
    expect(where()).toBe('/market-alerts?tab=stopped')
    await user.click(screen.getByRole('tab', { name: 'History' }))
    expect(where()).toBe('/market-alerts?tab=history')

    back()
    expect(where()).toBe('/elsewhere')
  })

  it('offers no New when market alerts are switched off on the server, and says so', async () => {
    api.state.mockResolvedValue(
      alertsState({
        enabled: false,
        alerts: [],
        engine: { code: 'off', line: 'Switched off on the server', real_time: false },
      })
    )
    renderPage()
    await settle()

    expect(screen.getByText('Switched off on the server')).toBeInTheDocument()
    expect(screen.getByText('Market alerts are switched off on the server.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New' })).toBeDisabled()
  })

  it('shows the server’s sentence when the service does not answer, and reads again on request', async () => {
    const sentence =
      'The alert service is not answering. Your alerts are kept; checking resumes when it is back.'
    api.state.mockRejectedValueOnce(refused('not_answering', sentence))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()

    expect(screen.getByText(sentence)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await settle()
    expect(screen.queryByText(sentence)).toBeNull()
    expect(screen.getByText('Watching · Real-time')).toBeInTheDocument()
  })

  it('shows a family viewer the owner-only sentence and nothing else', async () => {
    api.state.mockRejectedValue(
      refused('owner_only', 'Only the account owner can manage market alerts.')
    )
    renderPage()
    await settle()

    expect(screen.getByText('Only the account owner can manage market alerts.')).toBeInTheDocument()
    expect(document.querySelector('[data-alert]')).toBeNull()
  })
})

describe('live prices on the cards', () => {
  it('shows the figure the alert watches and its distance, Real-time while ticks come', async () => {
    renderPage()
    await settle()
    tick('SBIN', 'NSE', { ltp: 100.25 })

    expect(within(card(12)).getByText('₹100.25')).toBeInTheDocument()
    expect(within(card(12)).getByText('· ₹0.25 above the level')).toBeInTheDocument()
    expect(card(12).querySelector('[data-feed="live"]')?.textContent).toBe('Real-time')
    expect(feed.live()).toEqual(['NSE:SBIN:LTP'])
  })

  it('follows a bid alert on the Quote stream, its distance from the bid', async () => {
    api.state.mockResolvedValue(
      alertsState({
        alerts: [alertRow({ params: { op: 'crosses_below', level: '99.50', source: 'bid' } })],
      })
    )
    renderPage()
    await settle()
    tick('SBIN', 'NSE', { ltp: 100.25, bid_price: 99.4 })

    expect(feed.live()).toEqual(['NSE:SBIN:Quote'])
    expect(within(card(12)).getByText('Bid')).toBeInTheDocument()
    expect(within(card(12)).getByText('· ₹0.10 below the level')).toBeInTheDocument()
  })

  it('greys a price whose ticks stopped while due, and never calls it Real-time', async () => {
    renderPage()
    await settle()
    tick('SBIN', 'NSE', { ltp: 100.25 })
    act(() =>
      feed.setState({ connectionState: 'disconnected', isConnected: false, isAuthenticated: false })
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STALE_MS + 2_000)
    })
    act(() => frames.flush())

    expect(card(12).textContent).not.toContain('Real-time')
    expect(card(12).querySelector('[data-stale]')).not.toBeNull()
    expect(card(12).querySelector('[data-feed="connecting"]')?.textContent).toMatch(
      /^Reconnecting · updated \d+s ago$/
    )
  })

  it('says the market is closed from the server’s session line, not from a calendar of its own', async () => {
    const closed = {
      code: 'outside_hours' as const,
      line: 'Market closed · checks from Thu 09:15',
      real_time: false,
    }
    api.state.mockResolvedValue(alertsState({ engine: closed, segments: { NSE_EQ: closed } }))
    renderPage()
    await settle()
    tick('SBIN', 'NSE', { ltp: 100.25 })

    expect(card(12).querySelector('[data-feed="closed"]')?.textContent).toBe('Market closed')
  })

  it('shows the server’s auction line in the closing auction, never Real-time or Delayed', async () => {
    const auction = { from: NOW - 60_000, close: NOW + 14 * 60_000, until: NOW + 19 * 60_000 }
    const check = {
      code: 'closing_auction' as const,
      line: 'Closing auction · price at about 15:30',
      real_time: false,
    }
    api.state.mockResolvedValue(alertsState({ alerts: [alertRow({ auction, check })] }))
    renderPage()
    await settle()
    tick('SBIN', 'NSE', { ltp: 100.25 })

    expect(card(12).querySelector('[data-feed]')).toBeNull()
    expect(within(card(12)).getAllByText('Closing auction · price at about 15:30')).toHaveLength(1)
  })

  it('follows at most 100 instruments, and says so', async () => {
    const alerts: AlertRow[] = Array.from({ length: LIVE_CAP + 1 }, (_, index) =>
      alertRow({ id: index + 1, symbol: `STOCK${index}` })
    )
    api.state.mockResolvedValue(alertsState({ alerts }))
    renderPage()
    await settle()

    expect(feed.live()).toHaveLength(LIVE_CAP)
    expect(
      screen.getByText('Prices follow the first 100 instruments on this tab.')
    ).toBeInTheDocument()
  })
})

describe('an alert’s sheet', () => {
  async function openSheet(user: ReturnType<typeof userEvent.setup>, id = 12) {
    await user.click(card(id))
    return screen.getByRole('dialog')
  }

  it('opens from its card, and Back closes it before leaving the page', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()

    const sheet = await openSheet(user)
    expect(where()).toBe('/market-alerts?alert=12')
    expect(within(sheet).getByText('Crosses above ₹100.00')).toBeInTheDocument()

    back()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(where()).toBe('/market-alerts')
    back()
    expect(where()).toBe('/elsewhere')
  })

  it.each([
    ['active', 'Pause', ['Resume', 'Restart']],
    ['paused', 'Resume', ['Pause', 'Restart']],
    ['stopped', 'Restart', ['Pause', 'Resume']],
  ] as const)('offers a %s alert %s, Delete and its chart', async (status, move, others) => {
    api.state.mockResolvedValue(alertsState({ alerts: [alertRow({ status })] }))
    renderPage(['/market-alerts?alert=12'])
    await settle()

    const sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('button', { name: move })).toBeEnabled()
    for (const other of others)
      expect(within(sheet).queryByRole('button', { name: other })).toBeNull()
    expect(within(sheet).getByRole('button', { name: 'Delete' })).toBeEnabled()
    expect(within(sheet).getByRole('link', { name: 'Chart' })).toHaveAttribute(
      'href',
      '/chart/NSE/SBIN'
    )
  })

  it('pauses once however fast it is tapped, and shows the answer', async () => {
    let answer: (row: AlertRow) => void = () => {}
    api.change.mockReturnValue(
      new Promise<AlertRow>((resolve) => {
        answer = resolve
      })
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    const sheet = await openSheet(user)

    await user.click(within(sheet).getByRole('button', { name: 'Pause' }))
    expect(within(sheet).getByRole('button', { name: 'Pausing' })).toBeDisabled()
    await user.click(within(sheet).getByRole('button', { name: 'Pausing' }))
    expect(api.change).toHaveBeenCalledTimes(1)
    expect(api.change).toHaveBeenCalledWith(12, { version: 3, action: 'pause' })

    await act(async () => answer(alertRow({ version: 4, status: 'paused' })))
    await settle()
    expect(within(sheet).getByRole('button', { name: 'Resume' })).toBeEnabled()
    expect(card(12).querySelector('[data-status]')?.textContent).toBe('Paused')
  })

  it('asks before deleting, sends once, and drops the card', async () => {
    api.remove.mockResolvedValue({ deleted: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    const sheet = await openSheet(user)

    await user.click(within(sheet).getByRole('button', { name: 'Delete' }))
    const ask = screen.getByRole('dialog', { name: 'Delete this alert?' })
    expect(within(ask).getByText('Its history stays.')).toBeInTheDocument()
    expect(api.remove).not.toHaveBeenCalled()

    const yes = within(ask).getByRole('button', { name: 'Delete' })
    await user.dblClick(yes)
    await settle()

    expect(api.remove).toHaveBeenCalledTimes(1)
    expect(api.remove).toHaveBeenCalledWith(12)
    expect(card(12)).toBeNull()
    expect(toasts.success).toHaveBeenCalledWith('Alert deleted. Its history stays.')
    await waitFor(() => expect(where()).toBe('/market-alerts'))
  })

  it('shows a refusal’s sentence as it came, and reads the alerts again', async () => {
    const sentence = 'This alert was changed on another screen. Open it again, then try again.'
    api.change.mockRejectedValue(refused('conflict', sentence))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    const sheet = await openSheet(user)
    const reads = api.state.mock.calls.length

    await user.click(within(sheet).getByRole('button', { name: 'Pause' }))
    await settle()

    expect(toasts.error).toHaveBeenCalledWith(sentence)
    expect(api.state.mock.calls.length).toBe(reads + 1)
    expect(within(sheet).getByRole('button', { name: 'Pause' })).toBeEnabled()
  })

  it('drops the card of an alert already gone', async () => {
    api.change.mockRejectedValue(refused('gone', 'This alert has been deleted.'))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    const sheet = await openSheet(user)

    await user.click(within(sheet).getByRole('button', { name: 'Pause' }))
    await settle()

    expect(toasts.info).toHaveBeenCalledWith('This alert has been deleted.')
    expect(card(12)).toBeNull()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('holds a delete that got no answer, and reads the alerts again to see where it stands', async () => {
    api.remove.mockRejectedValue(refused('unanswered', 'No answer came from OpenAlgo.'))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    const sheet = await openSheet(user)
    const reads = api.state.mock.calls.length

    await user.click(within(sheet).getByRole('button', { name: 'Delete' }))
    await user.click(
      within(screen.getByRole('dialog', { name: 'Delete this alert?' })).getByRole('button', {
        name: 'Delete',
      })
    )
    await settle()

    expect(toasts.warning).toHaveBeenCalledTimes(1)
    expect(api.state.mock.calls.length).toBe(reads + 1)
    expect(within(sheet).getByText(/No answer came for this delete/)).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Delete' })).toBeDisabled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_ANSWER_HOLD_MS)
    })
    expect(within(sheet).getByRole('button', { name: 'Delete' })).toBeEnabled()
  })

  it('closes when the alert it names is not there', async () => {
    renderPage(['/elsewhere', '/market-alerts?alert=99'])
    await settle()
    await waitFor(() => expect(where()).toBe('/market-alerts'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('the form on the page', () => {
  it('opens a new alert’s form from New, and Back closes it before leaving the page', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()

    await user.click(screen.getByRole('button', { name: 'New' }))
    expect(where()).toBe('/market-alerts?sheet=new')
    expect(screen.getByRole('dialog', { name: 'New alert' })).toBeInTheDocument()

    back()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(where()).toBe('/market-alerts')
  })

  it('edits from the sheet on the alert’s own settings, then shows what was saved', async () => {
    api.change.mockResolvedValue(
      alertRow({
        version: 4,
        words: { condition: 'Crosses above ₹101.00', meta: 'Once', checks_at: null },
      })
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()

    await user.click(card(12))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Edit' }))
    expect(where()).toBe('/market-alerts?alert=12&sheet=edit')
    const form = screen.getByRole('dialog', { name: 'Edit alert' })
    expect(within(form).getByLabelText('Price level')).toHaveValue('100')

    await user.click(within(form).getByRole('button', { name: 'Save' }))
    await settle()
    expect(api.change.mock.calls[0][1]).toMatchObject({ version: 3, action: 'edit' })
    await waitFor(() => expect(where()).toBe('/market-alerts?alert=12'))
    expect(
      within(screen.getByRole('dialog')).getByText('Crosses above ₹101.00')
    ).toBeInTheDocument()

    back()
    expect(where()).toBe('/market-alerts')
  })
})
