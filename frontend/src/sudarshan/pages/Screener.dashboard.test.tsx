/**
 * The Dashboard tab (M9.16) in jsdom: it is the first tab and the home of the Screener (no `tab`
 * in the address), Screens and the Library keep their own addresses, moving between tabs replaces
 * the history entry, the Add a widget sheet is in the address so Back closes it first, a layout
 * that is only a suggestion shows like any other, and cards the read had no time for are read one
 * by one afterwards.
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
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { addressOf } from '@/sudarshan/screener/useScreenerAddress'
import { dashboard, resultRow, screenerState, screenRow, widgetCard } from '@/test/screener'
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
const cardsShown = () =>
  Array.from(document.querySelectorAll('[data-widget]')).map((card) =>
    card.getAttribute('data-widget')
  )
const back = () => act(() => history.go(-1))

beforeEach(() => {
  sockets.current = null
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.state.mockResolvedValue(
    screenerState([
      screenRow({ id: 1, name: 'One' }),
      screenRow({ id: 2, name: 'Two' }),
      screenRow({ id: 3, name: 'Three' }),
    ])
  )
})

describe('the tab row and the address', () => {
  it('shows Dashboard first and selected with no tab in the address', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Dashboard', 'Screens', 'Library'])
    expect(tabs[0].getAttribute('aria-selected')).toBe('true')
    expect(where()).toBe('/screener')
    expect(await screen.findByText('One')).toBeTruthy()
    expect(api.dashboard).toHaveBeenCalledTimes(1)
  })

  it('keeps Screens at ?tab=screens and the Library at ?tab=library', async () => {
    renderPage(['/screener?tab=screens'])
    expect(await screen.findByRole('button', { name: /One/ })).toBeTruthy()
    expect(api.dashboard).not.toHaveBeenCalled()
    expect(screen.getByRole('tab', { name: 'Screens' }).getAttribute('aria-selected')).toBe('true')
  })

  it('moves between the tabs by replacing the entry, so Back leaves the page', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: 'Screens' }))
    expect(where()).toBe('/screener?tab=screens')
    await userEvent.click(screen.getByRole('tab', { name: 'Library' }))
    expect(where()).toBe('/screener?tab=library')
    await userEvent.click(screen.getByRole('tab', { name: 'Dashboard' }))
    expect(where()).toBe('/screener')
    back()
    await waitFor(() => expect(where()).toBe('/elsewhere'))
  })

  it('opens the Add a widget sheet in the address, and Back closes it first', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    const add = await screen.findByRole('button', { name: 'Add a widget' })
    expect(add.className).toContain('min-h-12')
    expect(add.querySelector('svg')).not.toBeNull()
    await userEvent.click(add)
    expect(where()).toBe('/screener?sheet=add')
    expect(await screen.findByRole('dialog')).toBeTruthy()
    back()
    await waitFor(() => expect(where()).toBe('/screener'))
    expect(screen.queryByRole('dialog')).toBeNull()
    back()
    await waitFor(() => expect(where()).toBe('/elsewhere'))
  })

  it('reads a widget from its screen and kind, never an id', () => {
    const widget = addressOf(new URLSearchParams('sheet=widget&s=7&k=table'))
    expect(widget.widget).toEqual({ screen: 7, kind: 'table' })
    expect(widget.key).toBeNull()
    const symbol = addressOf(new URLSearchParams('sheet=symbol&k=NSE:INFY&s=7&wk=count'))
    expect(symbol.key).toBe('NSE:INFY')
    expect(symbol.from).toEqual({ screen: 7, kind: 'count' })
    expect(addressOf(new URLSearchParams('sheet=widget&s=7&k=chart')).widget).toBeNull()
    expect(addressOf(new URLSearchParams('sheet=widget&k=table')).widget).toBeNull()
  })
})

describe('the dashboard view', () => {
  it('shows a suggested layout, version 0, like any other: one card each, in its order', async () => {
    api.dashboard.mockResolvedValue(
      dashboard([widgetCard({ screen_id: 2 }), widgetCard({ screen_id: 1 })])
    )
    renderPage()
    await screen.findByText('Two')
    expect(cardsShown()).toEqual(['2:table', '1:table'])
    expect(
      within(document.querySelector('[data-widget]') as HTMLElement).getByText('Active')
    ).toBeTruthy()
  })

  it("says so when nothing is on the dashboard, in the screener's words", async () => {
    api.dashboard.mockResolvedValue(dashboard([], { suggested: false, version: 3 }))
    renderPage()
    expect(await screen.findByText('Nothing on the dashboard yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a widget' })).toBeTruthy()
  })

  it('lays the cards out in one column, two from 768 px and three from 1,200 px', async () => {
    api.dashboard.mockResolvedValue(dashboard())
    renderPage()
    const list = (await screen.findByText('One')).closest('ul[class*="grid"]') as HTMLElement
    expect(list.className).toContain('grid-cols-1')
    expect(list.className).toContain('md:grid-cols-2')
    expect(list.className).toContain('min-[75rem]:grid-cols-3')
  })

  it("reads the cards past the read's budget one by one, in card order, and fills them in place", async () => {
    api.dashboard.mockResolvedValue(
      dashboard([
        widgetCard({ screen_id: 1 }),
        widgetCard({ screen_id: 2, name: 'Two', rows_pending: true, rows_shown: [], run: null }),
        widgetCard({ screen_id: 3, name: 'Three', rows_pending: true, rows_shown: [], run: null }),
      ])
    )
    const order: number[] = []
    api.widget.mockImplementation(async (id: number) => {
      order.push(id)
      return widgetCard({ screen_id: id, rows_shown: [resultRow(id === 2 ? 'TCS' : 'ITC')] })
    })
    renderPage()
    expect(await screen.findByText('TCS')).toBeTruthy()
    expect(await screen.findByText('ITC')).toBeTruthy()
    expect(order).toEqual([2, 3])
    expect(api.widget).toHaveBeenCalledWith(2, 'table', expect.anything())
    expect(api.dashboard).toHaveBeenCalledTimes(1)
    expect(cardsShown()).toEqual(['1:table', '2:table', '3:table'])
  })

  it('says the read failed and offers Try again', async () => {
    api.dashboard.mockRejectedValue(new Error('x'))
    renderPage()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
