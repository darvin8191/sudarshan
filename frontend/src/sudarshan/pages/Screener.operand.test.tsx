/**
 * The operand sheet on the Screener page (M9.3): a value chip of a condition opens it over the
 * editor, with the tabs, the search, the parameters at their defaults, the timeframe and offset
 * chips, and the service's own words for what cannot be used. Nothing applies until Use this.
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
  preview: vi.fn(),
  universes: vi.fn(),
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
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: null }),
}))

import { createQueryClient } from '@/app/providers'
import { resetScreenActionsForTests } from '@/sudarshan/screener/actions'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { screenerState, screenRow } from '@/test/screener'
import Screener from './Screener'

vi.setConfig({ testTimeout: 20_000 })

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
const clauseCard = (id: string) => document.querySelector(`[data-clause="${id}"]`) as HTMLElement

beforeEach(() => {
  resetScreenActionsForTests()
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  api.state.mockResolvedValue(screenerState([screenRow()]))
  api.universes.mockResolvedValue({ universes: [] })
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.preview.mockResolvedValue({
    label: 'x',
    count: 0,
    first: [],
    not_checked: { count: 0, reasons: [] },
  })
})
afterEach(() => vi.useRealTimers())

const openLeft = async () => {
  await userEvent.click(await screen.findByRole('button', { name: /Value: Close/ }))
}
const pickIndicator = async (name: string) => {
  await userEvent.click(screen.getByRole('button', { name: 'Change' }))
  await userEvent.click(screen.getByRole('tab', { name: 'Indicators' }))
  await userEvent.click(screen.getByRole('button', { name }))
}

describe('the operand sheet', () => {
  it('opens on the value that is there, over the editor, and Back closes it without applying', async () => {
    renderPage(['/elsewhere', '/screener?new=1'])
    await openLeft()
    expect(where()).toBe('/screener?new=1&sheet=operand&c=c1&side=left')
    expect(screen.getByText('Pick a value')).toBeTruthy()
    await pickIndicator('RSI')
    expect(screen.getByLabelText('Length')).toBeTruthy()
    back()
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
    expect(within(clauseCard('c1')).getByRole('button', { name: /Value: Close/ })).toBeTruthy()
  })

  it('applies an indicator with its parameters and timeframe into the sentence', async () => {
    renderPage(['/screener?new=1'])
    await openLeft()
    await pickIndicator('EMA')
    const length = screen.getByLabelText('Length') as HTMLInputElement
    expect(length.value).toBe('20')
    await userEvent.clear(length)
    await userEvent.type(length, '50')
    await userEvent.click(screen.getByRole('button', { name: '5 min' }))
    await userEvent.click(screen.getByRole('button', { name: 'Use this' }))
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
    const card = clauseCard('c1')
    expect(within(card).getByRole('button', { name: 'Candles: 5 min' })).toBeTruthy()
    expect(within(card).getByRole('button', { name: 'Value: EMA 50' })).toBeTruthy()
  })

  it('blocks Use this on a parameter out of range, in the service words', async () => {
    renderPage(['/screener?new=1'])
    await openLeft()
    await pickIndicator('RSI')
    const length = screen.getByLabelText('Length')
    await userEvent.clear(length)
    await userEvent.type(length, '1')
    expect(screen.getByText('RSI length must be a whole number from 2 to 200.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Use this' }) as HTMLButtonElement).disabled).toBe(
      true
    )
  })

  it('offers the offsets, refuses a look-back past 100, and a first candle on a day candle', async () => {
    renderPage(['/screener?new=1'])
    await openLeft()
    await userEvent.click(screen.getByRole('button', { name: 'Some candles ago' }))
    const ago = screen.getByLabelText('How many candles ago')
    await userEvent.clear(ago)
    await userEvent.type(ago, '101')
    expect(screen.getByText('You can look back 100 candles at most.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Use this' }) as HTMLButtonElement).disabled).toBe(
      true
    )
    await userEvent.clear(ago)
    await userEvent.type(ago, '2')
    await userEvent.click(screen.getByRole('button', { name: 'Day' }))
    expect(
      (screen.getByRole('button', { name: "Today's first candle" }) as HTMLButtonElement).disabled
    ).toBe(true)
    expect(
      screen.getByText(
        "Today's first candle is for minute candles. For day candles, use 1 candle ago."
      )
    ).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Use this' }))
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
    const card = clauseCard('c1')
    expect(within(card).getByRole('button', { name: 'Which candle: 2 candles ago' })).toBeTruthy()
    expect(within(card).getByRole('button', { name: 'Candles: Day' })).toBeTruthy()
  })

  it('searches every tab by name', async () => {
    renderPage(['/screener?new=1'])
    await openLeft()
    await userEvent.click(screen.getByRole('button', { name: 'Change' }))
    await userEvent.type(screen.getByRole('searchbox'), 'pcr')
    expect(screen.getByRole('button', { name: 'PCR' })).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    await userEvent.clear(screen.getByRole('searchbox'))
    await userEvent.type(screen.getByRole('searchbox'), 'qqqq')
    expect(screen.getByText(/Nothing is called that/)).toBeTruthy()
  })

  it('replaces a typed number with an indicator, and a value with a number', async () => {
    renderPage(['/screener?new=1'])
    await screen.findByLabelText('Name')
    await userEvent.click(
      within(clauseCard('c1')).getByRole('button', { name: 'Pick a different value, second side' })
    )
    expect(where()).toBe('/screener?new=1&sheet=operand&c=c1&side=right')
    const number = screen.getByLabelText('Number')
    await userEvent.clear(number)
    await userEvent.type(number, 'abc')
    expect(screen.getByText('Type a number, like 55 or 2500.50.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Use this' }) as HTMLButtonElement).disabled).toBe(
      true
    )
    await userEvent.clear(number)
    await userEvent.type(number, '2500.5')
    await userEvent.click(screen.getByRole('button', { name: 'Use this' }))
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
    expect(
      (within(clauseCard('c1')).getByLabelText('Number, second side') as HTMLInputElement).value
    ).toBe('2500.5')
  })

  it('writes the OI window and the option underlying chips', async () => {
    renderPage(['/screener?new=1'])
    await openLeft()
    await userEvent.click(screen.getByRole('button', { name: 'Change' }))
    await userEvent.click(screen.getByRole('tab', { name: 'OI & options' }))
    await userEvent.click(screen.getByRole('button', { name: 'OI build-up' }))
    await userEvent.click(screen.getByRole('button', { name: 'The last few candles' }))
    expect(screen.getByLabelText('How many candles')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: '15 min' }))
    await userEvent.click(screen.getByRole('button', { name: 'Use this' }))
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
    expect(
      within(clauseCard('c1')).getByRole('button', { name: 'Value: OI build-up (3 candles)' })
    ).toBeTruthy()
  })
})
