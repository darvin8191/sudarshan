/**
 * "Screener today" on the dashboard (M9 UI8): per active screen the matches and who entered and
 * left, the latest changes, the unseen alerts, no figures passed off as current while the page is
 * not in the owner's room or the market is closed, nothing at all when there is nothing to show,
 * and a pushed run moving it with no read on a timer.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  today: vi.fn(),
  runsSince: vi.fn(),
  results: vi.fn(),
  runs: vi.fn(),
  membership: vi.fn(),
}))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))
const sockets = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: sockets.current }),
}))

import { createQueryClient } from '@/app/providers'
import { FIGURES_BEHIND, ScreenerCard } from '@/sudarshan/dashboard/ScreenerCard'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import type { Today } from '@/sudarshan/screener/types'
import { fakeSocket, NOW, screenerState, screenRow } from '@/test/screener'

const TODAY: Today = {
  screens: [
    { id: 1, name: '15-min EMA cross', entered: 3, exited: 1, matches: 7 },
    { id: 2, name: 'Volume spike', entered: 0, exited: 0, matches: 0 },
  ],
  recent: [
    { symbol: 'INFY', exchange: 'NSE', kind: 'entered', at: NOW, screen: '15-min EMA cross' },
    { symbol: 'TCS', exchange: 'NSE', kind: 'exited', at: NOW - 60_000, screen: 'Volume spike' },
  ],
  unseen_alerts: 2,
}

let client: ReturnType<typeof createQueryClient>

function show(open: boolean | null = true) {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ScreenerCard marketOpen={() => open} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

async function joined(room: ReturnType<typeof fakeSocket>) {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
  const join = room.emits.find((each) => each.event === 'sudarshan_screener_join')
  act(() => join?.ack(null, { ok: true }))
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
}

beforeEach(() => {
  client = createQueryClient()
  sockets.current = null
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.runsSince.mockResolvedValue({ runs: [], more: false })
  api.state.mockResolvedValue(
    screenerState([screenRow({ id: 1 }), screenRow({ id: 2, name: 'Volume spike' })])
  )
  api.today.mockResolvedValue(TODAY)
})
afterEach(() => client.clear())

describe('Screener today', () => {
  it('shows each screen with its matches, entries and exits, in words as well as signs', async () => {
    const room = fakeSocket()
    sockets.current = room.socket
    show()
    const first = (await screen.findByText('15-min EMA cross')).closest('a') as HTMLElement
    expect(first).toHaveAttribute('href', '/screener?screen=1')
    expect(within(first).getByText('7')).toBeInTheDocument()
    expect(within(first).getByText(/entered/)).toBeInTheDocument()
    expect(within(first).getByText(/exited/)).toBeInTheDocument()
    const latest = screen.getByRole('list', { name: 'Latest changes' })
    expect(within(latest).getByText(/INFY/)).toBeInTheDocument()
    expect(within(latest).getByText(/TCS/)).toBeInTheDocument()
    expect(screen.getByText('2 new alerts')).toBeInTheDocument()
  })

  it('says the figures may be behind while the page is not in the owner room', async () => {
    const room = fakeSocket()
    sockets.current = room.socket
    show()
    await screen.findByText('15-min EMA cross')
    expect(screen.getByText(FIGURES_BEHIND)).toBeInTheDocument()
  })

  it('does not say so once the room is joined', async () => {
    const room = fakeSocket()
    sockets.current = room.socket
    show()
    await screen.findByText('15-min EMA cross')
    await joined(room)
    expect(screen.queryByText(FIGURES_BEHIND)).not.toBeInTheDocument()
  })

  it('says the market is closed, and that the figures are from the last check', async () => {
    const room = fakeSocket()
    sockets.current = room.socket
    show(false)
    await screen.findByText('15-min EMA cross')
    await joined(room)
    expect(await screen.findByText(/Market closed/)).toBeInTheDocument()
  })

  it('shows nothing with no active screen, and nothing when the screener is off', async () => {
    api.state.mockResolvedValue(screenerState([screenRow({ status: 'paused' })]))
    show()
    await act(async () => {
      for (let i = 0; i < 10; i += 1) await Promise.resolve()
    })
    expect(screen.queryByText('Screener today')).not.toBeInTheDocument()
    expect(api.today).not.toHaveBeenCalled()
  })

  it('shows nothing when today cannot be read', async () => {
    api.today.mockRejectedValue(new Error('x'))
    show()
    await act(async () => {
      for (let i = 0; i < 10; i += 1) await Promise.resolve()
    })
    expect(screen.queryByText('Screener today')).not.toBeInTheDocument()
  })
})
