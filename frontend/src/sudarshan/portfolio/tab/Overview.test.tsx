/**
 * The Overview of a custom tab in jsdom (M7.23). Pinned: Summary, Today, holdings as cards and as a
 * table, top movers; "No price yet" and "not known yet" are words, never 0; an old NAV carries a
 * clock and the word; "Real-time" appears only once a tick newer than the read arrives.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))

import { createQueryClient } from '@/app/providers'
import { feed } from '@/test/marketDataHarness'
import { tab } from '../sheets/fixtures'
import { row, view } from './fixtures'
import { TabOverview } from './Overview'

function show(data: object, part: 'overview' | 'holdings' = 'overview') {
  api.call.mockImplementation(async (op: string) => {
    if (op === 'tab_get') return { enabled: true, data }
    throw new Error(`unexpected op ${op}`)
  })
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <TabOverview tab={tab(2, 'Gold')} part={part} onAdd={() => {}} onImport={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.call.mockReset()
  feed.reset()
})

describe('the Overview', () => {
  it('shows the Summary and Today with signs, arrows and the read time', async () => {
    show(
      view([
        row(1, 'Alpha', { price: 1_100_000 }), // value 1,100 on cost 900
        row(2, 'Beta', { price: 900_000 }), // value 900 on cost 900
      ])
    )
    const summary = await screen.findByRole('region', { name: 'Summary' })
    expect(within(summary).getByText('₹2,000.00')).toBeTruthy() // value, in Khand
    expect(within(summary).getByText('Invested in current holdings ₹1,800.00')).toBeTruthy()
    expect(within(summary).getAllByText('+₹200.00')).toHaveLength(2) // overall gain and unrealised
    const today = screen.getByRole('region', { name: 'Today' })
    expect(within(today).getByText('+₹20.00')).toBeTruthy() // +110 and −90 against yesterday's close
    expect(screen.getByText('Read 10:41:05')).toBeTruthy()
    expect(screen.queryByText('Real-time')).toBeNull() // no tick yet
  })

  it('says Today is not known yet, and a holding has no price yet, never a zero', async () => {
    show(
      view([
        row(1, 'Alpha', { prev: null }),
        row(2, 'Gamma', { price: null, prev: null, day: null }),
      ])
    )
    await screen.findByRole('region', { name: 'Summary' })
    const today = screen.getByRole('region', { name: 'Today' })
    expect(within(today).getByText('Today: not known yet')).toBeTruthy()
    const cards = screen.getByRole('list', { name: 'Holdings cards' })
    expect(within(cards).getAllByText('No price yet').length).toBeGreaterThan(0)
    expect(within(cards).getAllByText('Not known yet').length).toBeGreaterThan(0)
    expect(screen.getByText('1 holding has no price yet and is counted at cost.')).toBeTruthy()
  })

  it('marks an old NAV with a clock and the word Old', async () => {
    show(
      view([
        row(3, 'Fund', {
          kind: 'mf',
          fresh: { state: 'old', words: 'NAV of 21 Oct; AMFI has not published a newer one.' },
          mode: 'pending',
        }),
      ])
    )
    const cards = await screen.findByRole('list', { name: 'Holdings cards' })
    expect(within(cards).getByText(/Old ·/)).toBeTruthy()
    expect(within(cards).getByText(/NAV of 21 Oct/)).toBeTruthy()
    expect(within(cards).getByText('Waiting for the NAV')).toBeTruthy()
  })

  it('lists the best and worst movers', async () => {
    show(view([row(1, 'Alpha', { price: 1_100_000 }), row(2, 'Beta', { price: 900_000 })]))
    const movers = await screen.findByRole('region', { name: 'Top movers' })
    expect(
      within(within(movers).getByRole('list', { name: 'Best today' })).getByText('Alpha')
    ).toBeTruthy()
    expect(
      within(within(movers).getByRole('list', { name: 'Worst today' })).getByText('Beta')
    ).toBeTruthy()
  })

  it('draws a table with the layout columns beside the cards', async () => {
    show(view([row(1, 'Alpha')]), 'holdings')
    const table = await screen.findByRole('table')
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((c) => c.textContent)
    ).toEqual(['Name', 'Quantity', 'Average cost', 'Price', 'Today', 'Value', 'Gain', 'Weight'])
    expect(screen.queryByRole('region', { name: 'Summary' })).toBeNull() // Holdings part only
  })

  it('moves with a tick newer than the read, and then says Real-time', async () => {
    show(view([row(1, 'ALPHA', { symbol: 'ALPHA' })]))
    await screen.findByRole('region', { name: 'Summary' })
    act(() => feed.push('ALPHA', 'NSE', { ltp: 110 })) // 10 units: value 1,100
    await waitFor(() => expect(screen.getAllByText('Real-time').length).toBeGreaterThan(0))
    const summary = screen.getByRole('region', { name: 'Summary' })
    expect(within(summary).getByText('₹1,100.00')).toBeTruthy()
  })

  it('says what to do on an empty tab', async () => {
    show(view([]))
    expect(await screen.findByText('Nothing here yet. Add an entry or import a file.')).toBeTruthy()
  })
})
