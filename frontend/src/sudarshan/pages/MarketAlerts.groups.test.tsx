/**
 * The Alerts page with list alerts (M8.14): a group is one card, its member alerts are not cards
 * of their own and show, with their prices followed, only while it is expanded, inside the page's
 * 100 instruments; a push updates and removes cards; the list form and a group's form open from
 * the address, and Back closes them first; with no groups the page is as it was.
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
  events: vi.fn(),
  prefs: vi.fn(),
  createGroup: vi.fn(),
  changeGroup: vi.fn(),
  removeGroup: vi.fn(),
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

import type { AlertRow } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { resetAlertActionsForTests } from '@/sudarshan/alerts/actions'
import { resetGroupActionsForTests } from '@/sudarshan/alerts/groupActions'
import { resetMarketAlertsForTests } from '@/sudarshan/alerts/marketAlerts'
import { applyStatePush, STATE_KEY } from '@/sudarshan/alerts/stateCache'
import { groupRow, memberRow } from '@/sudarshan/alerts/testGroups'
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
const card = (id: number) => document.querySelector(`[data-alert="${id}"]`) as HTMLElement | null
const group = (id: number) => document.querySelector(`[data-group="${id}"]`) as HTMLElement | null

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
  act(() => frames.flush())
}

const members = [memberRow(21, 'SBIN'), memberRow(22, 'TCS'), memberRow(23, 'INFY')]

function withGroup(extra: Partial<Parameters<typeof alertsState>[0]> = {}) {
  return alertsState({
    alerts: [alertRow({ id: 12, symbol: 'RELIANCE' }), ...members],
    groups: [groupRow()],
    groups_by_ref: { 'list:swing': [7] },
    ...extra,
  })
}

beforeEach(() => {
  fakeClock()
  vi.setSystemTime(NOW)
  feed.reset()
  frames.install()
  resetMarketAlertsForTests()
  resetAlertActionsForTests()
  resetGroupActionsForTests()
  for (const fn of [...Object.values(api), ...Object.values(toasts)]) fn.mockReset()
  api.state.mockResolvedValue(withGroup())
  api.events.mockResolvedValue({ events: [], more: false })
  api.prefs.mockResolvedValue({ telegram: true, app: true })
  useAuthStore.setState({ apiKey: 'key', sessionChecked: true, brokerSessionExpired: false })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the group card among the cards', () => {
  it('draws one card for the group and no card for its member alerts', async () => {
    renderPage()
    await settle()
    expect(
      within(group(7) as HTMLElement).getByText(
        'Swing · % move 2% · 24 symbols · Active · follows the list'
      )
    ).toBeInTheDocument()
    expect(card(12)).not.toBeNull()
    for (const row of members) expect(card(row.id)).toBeNull()
    expect(screen.getByRole('tab', { name: /Active/ })).toHaveTextContent('(2)')
  })

  it('shows the members and follows their prices only while it is expanded', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    expect(feed.live()).toEqual(['NSE:RELIANCE:LTP'])

    await user.click(screen.getByRole('button', { name: /Show the alerts/ }))
    await settle()
    for (const row of members) expect(card(row.id)).not.toBeNull()
    expect(feed.live().sort()).toEqual([
      'NSE:INFY:LTP',
      'NSE:RELIANCE:LTP',
      'NSE:SBIN:LTP',
      'NSE:TCS:LTP',
    ])

    await user.click(screen.getByRole('button', { name: 'Hide the alerts' }))
    await settle()
    expect(card(21)).toBeNull()
    expect(feed.live()).toEqual(['NSE:RELIANCE:LTP'])
  })

  it('counts the expanded members in the 100, and says when some show no price', async () => {
    const loose: AlertRow[] = Array.from({ length: LIVE_CAP - 1 }, (_, index) =>
      alertRow({ id: 100 + index, symbol: `STOCK${index}` })
    )
    api.state.mockResolvedValue(withGroup({ alerts: [...loose, ...members] }))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    expect(feed.live()).toHaveLength(LIVE_CAP - 1)

    await user.click(screen.getByRole('button', { name: /Show the alerts/ }))
    await settle()
    expect(feed.live()).toHaveLength(LIVE_CAP)
    expect(
      screen.getByText(
        'Prices follow the first 100 instruments on this tab. 2 symbols of the list alerts you opened show no price.'
      )
    ).toBeInTheDocument()
  })

  it('lists a stopped group under Stopped', async () => {
    api.state.mockResolvedValue(
      withGroup({
        groups: [
          groupRow({
            status: 'stopped',
            status_reason: 'The list Swing was deleted, so this alert stopped.',
          }),
        ],
      })
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    expect(group(7)).toBeNull()
    await user.click(screen.getByRole('tab', { name: /Stopped/ }))
    expect(
      within(group(7) as HTMLElement).getByText(
        'The list Swing was deleted, so this alert stopped.'
      )
    ).toBeInTheDocument()
  })
})

describe('pushes', () => {
  it('updates a card’s words and drops a deleted card with its alerts', async () => {
    const client = renderPage()
    await settle()
    const words =
      'Covers 20 of 24: alerts can follow at most 200 instruments and 500 Active alerts.'
    const engine = alertsState().engine
    act(() => {
      client.setQueryData(
        STATE_KEY,
        applyStatePush(client.getQueryData(STATE_KEY) as never, {
          engine,
          alerts: [],
          removed: [],
          groups: [
            {
              id: 7,
              version: 2,
              status: 'active',
              status_reason: words,
              covered: 20,
              left_out: [],
              synced_at: NOW + 5,
            },
          ],
          server_time: NOW + 1_000,
        }).state
      )
    })
    await settle()
    expect(within(group(7) as HTMLElement).getByText(words)).toBeInTheDocument()
    expect(within(group(7) as HTMLElement).getByText(/20 symbols/)).toBeInTheDocument()

    act(() => {
      client.setQueryData(
        STATE_KEY,
        applyStatePush(client.getQueryData(STATE_KEY) as never, {
          engine,
          alerts: [],
          removed: [21, 22, 23],
          removed_groups: [7],
          server_time: NOW + 2_000,
        }).state
      )
    })
    await settle()
    expect(group(7)).toBeNull()
    expect(card(12)).not.toBeNull()
  })
})

describe('the forms from the address', () => {
  it('opens the list alert form from the contract’s address, and Back closes it first', async () => {
    renderPage([
      '/elsewhere',
      '/market-alerts',
      '/market-alerts?sheet=list&ref=list%3Aswing&name=Swing&count=24&fed=1',
    ])
    await settle()
    const sheet = screen.getByRole('dialog', { name: 'Alert on a list' })
    expect(within(sheet).getByText('Every symbol of Swing (24)')).toBeInTheDocument()
    expect(
      within(sheet).getByText('A screen feeds this list. Its own alerts may suit better.')
    ).toBeInTheDocument()
    back()
    expect(where()).toBe('/market-alerts')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens a group’s form from Edit on its card, and Back closes it before leaving the page', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await settle()
    await user.click(within(group(7) as HTMLElement).getByRole('button', { name: 'Edit' }))
    expect(where()).toBe('/market-alerts?group=7&sheet=group')
    const sheet = screen.getByRole('dialog', { name: 'Edit list alert' })
    expect(within(sheet).getByLabelText('Move, in %')).toHaveValue('2')
    back()
    expect(where()).toBe('/market-alerts')
    back()
    expect(screen.getByText('Another page')).toBeInTheDocument()
  })

  it('closes a group’s form when the group is not there, and says so', async () => {
    renderPage(['/elsewhere', '/market-alerts?sheet=group&group=99'])
    await settle()
    await waitFor(() => expect(toasts.info).toHaveBeenCalledWith('This list alert was deleted.'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens no list form with market alerts switched off', async () => {
    api.state.mockResolvedValue(alertsState({ enabled: false, alerts: [] }))
    renderPage(['/elsewhere', '/market-alerts?sheet=list&ref=list%3Aswing&name=Swing&count=24'])
    await settle()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('without list alerts', () => {
  it('is the page it was: every alert a card, no group card, no extra note', async () => {
    api.state.mockResolvedValue(
      alertsState({ alerts: [alertRow({ id: 12 }), alertRow({ id: 13, symbol: 'TCS' })] })
    )
    renderPage()
    await settle()
    expect(card(12)).not.toBeNull()
    expect(card(13)).not.toBeNull()
    expect(document.querySelector('[data-group]')).toBeNull()
    expect(screen.queryByRole('button', { name: /Show the alerts/ })).toBeNull()
    expect(screen.getByRole('tab', { name: /Active/ })).toHaveTextContent('(2)')
  })
})
