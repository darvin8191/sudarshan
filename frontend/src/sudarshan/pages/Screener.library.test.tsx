/**
 * The Library (M9.18) in jsdom: ready-made screens as cards, each one tap from a Paused copy made
 * once, a preset that cannot be used saying why with an icon and no button, and the new screen
 * opening on the page after it is made.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { type InitialEntry, MemoryRouter, Route, Routes, useLocation } from 'react-router'
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
import { ScreenerError } from '@/sudarshan/screener/api'
import { MADE_WORDS } from '@/sudarshan/screener/Library'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { fakeSocket, SPEC, screenerState, screenRow } from '@/test/screener'
import Screener from './Screener'

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}
const where = () => screen.getByTestId('where').textContent

function renderPage(entries: InitialEntry[]) {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Routes>
          <Route path="/screener" element={<Screener />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}

const CARDS = [
  {
    key: 'volume_spike',
    name: 'Volume spike',
    about: 'Volume well above its recent average.',
    usable: true,
    where: 'F&O stocks - 15-min',
    spec: { ...SPEC, name: 'Volume spike' },
    reason: null,
  },
  {
    key: 'delivery',
    name: 'High delivery',
    about: 'Delivery share well above normal.',
    usable: false,
    where: null,
    spec: null,
    reason: 'Needs the daily delivery file, not set up yet.',
  },
]

beforeEach(() => {
  sockets.current = fakeSocket().socket
  resetScreenActionsForTests()
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.state.mockResolvedValue(screenerState([]))
  api.presets.mockResolvedValue({ presets: CARDS })
})
afterEach(() => vi.useRealTimers())

describe('the Library', () => {
  it('opens from the tab and lists the cards', async () => {
    renderPage(['/screener'])
    await userEvent.click(await screen.findByRole('tab', { name: 'Library' }))
    expect(await screen.findByText('Volume spike')).toBeInTheDocument()
    expect(where()).toBe('/screener?tab=library')
    expect(screen.getByRole('tab', { name: 'Library' })).toHaveAttribute('aria-selected', 'true')
  })

  it('says why a preset cannot be used, and gives it no button', async () => {
    renderPage(['/screener?tab=library'])
    const card = (await screen.findByText('High delivery')).closest('li') as HTMLElement
    expect(within(card).getByText(/Needs the daily delivery file/)).toBeInTheDocument()
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
  })

  it('makes one Paused copy however many times it is tapped, then opens it', async () => {
    api.create.mockResolvedValue(
      screenRow({ id: 9, name: 'Volume spike', status: 'paused', next_check: null })
    )
    renderPage(['/screener?tab=library'])
    const use = await screen.findByRole('button', { name: 'Use this' })
    await userEvent.dblClick(use)
    await vi.waitFor(() => expect(where()).toBe('/screener?screen=9'))
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(api.create.mock.calls[0][0]).toMatchObject({ status: 'paused' })
    expect(api.create.mock.calls[0][0].spec.name).toBe('Volume spike')
    expect(toasts.success).toHaveBeenCalledWith(MADE_WORDS)
  })

  it('shows the server sentence when it refuses, and makes nothing', async () => {
    api.create.mockRejectedValue(
      new ScreenerError({ code: 'invalid', message: 'You already have 30 screens.', field: null })
    )
    renderPage(['/screener?tab=library'])
    await userEvent.click(await screen.findByRole('button', { name: 'Use this' }))
    expect(await screen.findByText('You already have 30 screens.')).toBeInTheDocument()
    expect(where()).toBe('/screener?tab=library')
  })

  it('says it could not be read, with a way to try again', async () => {
    api.presets.mockRejectedValue(new Error('x'))
    renderPage(['/screener?tab=library'])
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
