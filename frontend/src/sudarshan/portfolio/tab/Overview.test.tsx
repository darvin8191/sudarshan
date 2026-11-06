/**
 * The Overview of a custom tab in jsdom (M7.23). Pinned: Summary, Today, holdings as cards and as a
 * table, top movers; "No price yet" and "not known yet" are words, never 0; an old NAV carries a
 * clock and the word; "Real-time" appears only once a tick newer than the read arrives.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
import { report, row, view } from './fixtures'
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

  describe('allocation and filters', () => {
    const layout = (widgets: string[]) => ({
      ...view([]).layout,
      spec: { ...view([]).layout.spec, widgets },
    })
    const slices = {
      class: [
        { key: 'equity', label: 'Equity', value_paise: 60_000, pct: '60.0000', holdings: 1 },
        { key: 'gold', label: 'Gold and silver', value_paise: 40_000, pct: '40.0000', holdings: 1 },
      ],
    }

    function serveFiltered(rows: ReturnType<typeof row>[], extra: object = {}) {
      api.call.mockImplementation(
        async (op: string, args: { classes?: string[]; as_of?: number }) => {
          if (op !== 'tab_get') throw new Error(op)
          const keep = args.classes?.length
            ? rows.filter((r) => r.asset.asset_class === args.classes?.[0])
            : rows
          return {
            enabled: true,
            data: view(keep, {
              layout: layout(['allocation', 'holdings']),
              allocation: slices as never,
              as_of: args.as_of ?? null,
              ...extra,
            }),
          }
        }
      )
      return render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter>
            <TabOverview
              tab={tab(2, 'Gold')}
              part="overview"
              onAdd={() => {}}
              onImport={() => {}}
            />
          </MemoryRouter>
        </QueryClientProvider>
      )
    }

    const gold = () => {
      const r = row(2, 'Bar', { price: 900_000 })
      r.asset.asset_class = 'gold'
      return r
    }

    it('a tap on a bar reads the tab again with that class', async () => {
      serveFiltered([row(1, 'Alpha'), gold()])
      await userEvent.click(await screen.findByRole('button', { name: /Gold and silver/ }))
      await waitFor(() =>
        expect(api.call).toHaveBeenLastCalledWith('tab_get', { id: 2, classes: ['gold'] })
      )
      const cards = await screen.findByRole('list', { name: 'Holdings cards' })
      await waitFor(() => expect(within(cards).queryByText('Alpha')).toBeNull())
      expect(within(cards).getByText('Bar')).toBeTruthy()
      expect(
        within(screen.getByRole('list', { name: 'Allocation by class' }))
          .getByRole('button', { name: /Gold and silver/ })
          .getAttribute('aria-pressed')
      ).toBe('true')
    })

    it('says so, with a way out, when nothing matches', async () => {
      serveFiltered([row(1, 'Alpha')])
      await userEvent.click(await screen.findByRole('button', { name: /Gold and silver/ }))
      expect(await screen.findByText('No holding matches these filters.')).toBeTruthy()
      await userEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0])
      expect(await screen.findByRole('list', { name: 'Holdings cards' })).toBeTruthy()
    })

    it('as of a date reads with the day and says value on that day is not known', async () => {
      serveFiltered([row(1, 'Alpha', { price: null, day: null })])
      await userEvent.click(await screen.findByRole('button', { name: /Filters/ }))
      await userEvent.type(screen.getByLabelText('As of a date'), '2026-09-12')
      await waitFor(() =>
        expect(api.call).toHaveBeenLastCalledWith('tab_get', { id: 2, as_of: 20260912 })
      )
      expect(await screen.findByText(/Value on that day is not known/)).toBeTruthy()
      expect(screen.getByText('Value as on 12 Sep 2026')).toBeTruthy()
      expect(screen.queryByText('Real-time')).toBeNull()
    })
  })

  describe('the performance widgets', () => {
    function serveWith(widgets: string[], performance: () => unknown) {
      const data = view([row(1, 'Alpha')])
      data.layout.spec.widgets = widgets as never
      api.call.mockImplementation(async (op: string) => {
        if (op === 'tab_get') return { enabled: true, data }
        if (op === 'performance') return { enabled: true, data: performance() }
        throw new Error(`unexpected op ${op}`)
      })
      return render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter>
            <TabOverview
              tab={tab(2, 'Gold')}
              part="overview"
              onAdd={() => {}}
              onImport={() => {}}
            />
          </MemoryRouter>
        </QueryClientProvider>
      )
    }

    it('draws the return and the income where the layout puts them', async () => {
      serveWith(['summary', 'performance', 'income'], report)
      expect(await screen.findByRole('region', { name: 'Performance' })).toBeTruthy()
      expect(screen.getByRole('region', { name: 'Income' })).toBeTruthy()
      expect(screen.queryByRole('region', { name: 'Value against money put in' })).toBeNull()
    })

    it('says in words when the figures cannot be read, and tries again on request', async () => {
      let n = 0
      serveWith(['performance'], () => {
        n += 1
        if (n === 1) throw new Error('down')
        return report()
      })
      await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
      expect(await screen.findByRole('region', { name: 'Performance' })).toBeTruthy()
    })
  })
})
