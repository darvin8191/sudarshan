/**
 * The Alerts page with list alerts (M8.14): a group is one card, its member alerts are not cards
 * of their own and show, with their prices followed, only while it is expanded, inside the page's
 * 100 instruments; a push updates and removes cards; the list form and a group's form open from
 * the address, and Back closes them first; with no groups the page is as it was.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
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

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { resetAlertActionsForTests } from '@/sudarshan/alerts/actions'
import { resetGroupActionsForTests } from '@/sudarshan/alerts/groupActions'
import { resetMarketAlertsForTests } from '@/sudarshan/alerts/marketAlerts'
import { listAlertAddress } from '@/sudarshan/alerts/useAlertOverlay'
import { offerFor, sectionRef } from '@/sudarshan/watchlist/v2/listAlert'
import { fakeClock, frames } from '@/test/frames'
import { alertRow, alertsState, NOW } from '@/test/marketAlerts'
import { feed } from '@/test/marketDataHarness'
import MarketAlerts from './MarketAlerts'

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

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
    await vi.advanceTimersByTimeAsync(50)
  })
  act(() => frames.flush())
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
  api.state.mockResolvedValue(alertsState({ alerts: [alertRow({ id: 12, symbol: 'RELIANCE' })] }))
  api.events.mockResolvedValue({ events: [], more: false })
  api.prefs.mockResolvedValue({ telegram: true, app: true })
  useAuthStore.setState({ apiKey: 'key', sessionChecked: true, brokerSessionExpired: false })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const hrefOf = (ref: string, name: string, count: number, fed = false) =>
  offerFor({ ref, name, count, kind: 'list', fed }, { v2: true, alerts: true }, () => {}).href

describe('the address the menus build, opened', () => {
  it.each([
    ['a list', 'list:7', 'Swing', 24],
    ['a section', sectionRef('list:7', 2), 'Banks', 6],
  ])('opens the list form for %s with its heading', async (_what, ref, name, count) => {
    const href = hrefOf(ref, name, count) as string
    expect(href).toBe(listAlertAddress({ ref, name, count, fed: false }))
    renderPage(['/elsewhere', href])
    await settle()
    const sheet = screen.getByRole('dialog', { name: 'Alert on a list' })
    expect(within(sheet).getByText(`Every symbol of ${name} (${count})`)).toBeInTheDocument()
  })

  it('says a screen feeds the list when the menu passes fed', async () => {
    renderPage(['/elsewhere', hrefOf('list:7', 'Swing', 24, true) as string])
    await settle()
    expect(
      screen.getByText('A screen feeds this list. Its own alerts may suit better.')
    ).toBeInTheDocument()
  })
})
