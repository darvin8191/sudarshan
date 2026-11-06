/**
 * The Upstox tab in jsdom: the bridge is a stand-in answering as the contract says. Pinned: each
 * part says when it was read, ticks move the figures with sign and arrow, the service's sentences
 * show as written (and no zero stands in for a closed answer), Analyze mode says whose account
 * this is, and only an order_update re-reads (never an analyzer_update).
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
const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return this
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
      return this
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))

import { createQueryClient } from '@/app/providers'
import { useThemeStore } from '@/stores/themeStore'
import { feed } from '@/test/marketDataHarness'
import { holding, position, READ_AT } from './fixtures'
import { ANALYZE_WORDS, UpstoxTab } from './UpstoxTab'

const part = <T,>(data: T | null, extra: object = {}) => ({
  state: 'ok',
  read_at: READ_AT,
  note: null,
  data,
  ...extra,
})

function serve(book: object, history: object = { days: [], note: null }) {
  api.call.mockImplementation(async (op: string) => {
    if (op === 'upstox_book') return { enabled: true, data: book }
    if (op === 'upstox_history') return { enabled: true, data: history }
    throw new Error(`unexpected op ${op}`)
  })
}

function show(view: 'holdings' | 'positions' | 'funds' | 'history' = 'holdings') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <UpstoxTab view={view} onView={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.call.mockReset()
  feed.reset()
  useThemeStore.setState({ appMode: 'live' })
})

describe('Holdings', () => {
  it('reads the parts of the view, says when, and shows chips and links', async () => {
    serve({
      holdings: part([holding({ t1_qty_u: 2_000_000, collateral_qty_u: 3_000_000 })]),
    })
    show()
    await screen.findByText('INFY')
    expect(api.call).toHaveBeenCalledWith('upstox_book', {
      parts: ['holdings'],
      max_age: undefined,
    })
    expect(screen.getByText('Read 10:41:05')).toBeTruthy()
    expect(screen.getByText('T1 2')).toBeTruthy()
    expect(screen.getByText('Pledged 3')).toBeTruthy()
    expect(screen.getAllByText('+₹1,000.00').length).toBe(2) // Upstox P&L, with its plus sign
    expect(screen.getByRole('link', { name: /Portfolio Analyzer/ }).getAttribute('href')).toBe(
      '/portfolio-analyzer'
    )
  })

  it('moves the figures with a newer tick and keeps the sign', async () => {
    serve({ holdings: part([holding()]) })
    show()
    await screen.findByText('INFY')
    expect(screen.queryByText('Real-time')).toBeNull()
    act(() => feed.push('INFY', 'NSE', { ltp: 1490 }))
    await waitFor(() => expect(screen.getAllByText('+₹900.00').length).toBe(2)) // summary and card
    expect(screen.getAllByText('Real-time').length).toBeGreaterThan(0)
    act(() => feed.push('INFY', 'NSE', { ltp: 1400 }))
    // Today fell below zero: a true minus, not colour alone.
    await waitFor(() => expect(screen.getAllByText('−₹800.00').length).toBeGreaterThan(0))
  })

  it('opens a sheet with the Chart and Trade in Holdings links, and no order button', async () => {
    serve({ holdings: part([holding()]) })
    show()
    await userEvent.click(await screen.findByRole('button', { name: /INFY, details/ }))
    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByRole('link', { name: 'Chart' }).getAttribute('href')).toBe(
      '/chart/NSE/INFY'
    )
    expect(
      within(sheet).getByRole('link', { name: 'Trade in Holdings' }).getAttribute('href')
    ).toBe('/holdings')
    expect(within(sheet).queryByRole('button', { name: /buy|sell/i })).toBeNull()
  })

  it('shows Today as not known yet, never zero, for a holding with no previous close', async () => {
    serve({ holdings: part([holding({ close_price_u: null })]) })
    show()
    await screen.findByText('INFY')
    expect(screen.getAllByText(/not known yet/i).length).toBeGreaterThan(0)
  })

  it('keeps the figures and shows the service sentence when Upstox did not answer', async () => {
    serve({
      holdings: part([holding()], {
        state: 'stale',
        note: 'Upstox did not answer. Figures from 10:41:05.',
      }),
    })
    show()
    await screen.findByText('INFY')
    expect(screen.getByText('Upstox did not answer. Figures from 10:41:05.')).toBeTruthy()
  })

  it('shows the 401 sentence as it came, with no figures', async () => {
    const words =
      'Upstox did not accept SUDARSHAN’s read-only token, so the Upstox tab cannot be read now.'
    serve({ holdings: part(null, { state: 'unavailable', read_at: null, note: words }) })
    show()
    expect(await screen.findByText(words)).toBeTruthy()
    expect(screen.queryByText(/₹0/)).toBeNull()
  })

  it('says whose account this is in Analyze mode', async () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    serve({ holdings: part([holding()]) })
    show()
    expect(await screen.findByText(ANALYZE_WORDS)).toBeTruthy()
    expect(ANALYZE_WORDS).toBe('Your real Upstox account. Analyze-mode orders are not in it.')
  })
})

describe('re-reads', () => {
  it('an equity order_update reads again, with a fresh max age; an analyzer_update does not', async () => {
    serve({ holdings: part([holding()]) })
    show()
    await screen.findByText('INFY')
    const before = api.call.mock.calls.length
    act(() => socket.fire('analyzer_update', { request: {} }))
    await new Promise((resolve) => setTimeout(resolve, 700))
    expect(api.call.mock.calls.length).toBe(before)
    act(() =>
      socket.fire('order_update', {
        exchange: 'NSE',
        product: 'CNC',
        order_status: 'complete',
        filled_quantity: 1,
      })
    )
    await waitFor(() => expect(api.call.mock.calls.length).toBeGreaterThan(before))
    expect(api.call.mock.calls.at(-1)?.[1]).toEqual({ parts: ['holdings'], max_age: 2 })
  })
})

describe('Positions', () => {
  it('lists positions without actions, with the link to Open Positions', async () => {
    serve({
      positions: part([
        position(),
        position({
          instrument_key: 'k2',
          symbol: 'SBIN',
          exchange: 'NSE',
          qty_u: -5_000_000,
          pnl_paise: -2500,
          unrealised_paise: -2500,
          product: 'I',
        }),
      ]),
      mtf: part([]),
    })
    show('positions')
    await screen.findByText('NIFTY25NOV2625000CE')
    expect(screen.getByText(/Long 65/)).toBeTruthy()
    expect(screen.getByText(/Short 5/)).toBeTruthy()
    expect(screen.getByText('−₹25.00')).toBeTruthy() // a loss: true minus
    expect(screen.getByRole('link', { name: /Open Positions/ }).getAttribute('href')).toBe(
      '/positions'
    )
    expect(screen.queryByRole('button', { name: /exit|square|buy|sell/i })).toBeNull()
    expect(screen.getByText('Read 10:41:05')).toBeTruthy()
  })
})

describe('Funds', () => {
  const funds = {
    available_paise: 12_345_600,
    groups: [
      {
        key: 'cash',
        label: 'Cash',
        total_paise: 12_345_600,
        lines: [{ key: 'unpaid_charges', label: 'Unpaid charges', paise: -5000 }],
      },
    ],
  }

  it('shows the tree, each line labelled, with the read time', async () => {
    serve({ funds: part(funds) })
    show('funds')
    await screen.findByText('Unpaid charges')
    expect(screen.getByText('Available to trade')).toBeTruthy()
    expect(screen.getAllByText('₹1,23,456.00').length).toBeGreaterThan(0)
    expect(screen.getByText('−₹50.00')).toBeTruthy()
    expect(screen.getByText('Read 10:41:05')).toBeTruthy()
  })

  it('shows the closed-window sentence and no zeros when there is no earlier read', async () => {
    const words = 'Funds are available from 5:30 AM.'
    serve({ funds: part(null, { state: 'unavailable', read_at: null, note: words }) })
    show('funds')
    expect(await screen.findByText(words)).toBeTruthy()
    expect(screen.queryByText(/₹0/)).toBeNull()
  })
})

describe('History', () => {
  it('shows closing records, the last 10 days newest first, and the Analyzer link', async () => {
    const days = Array.from({ length: 12 }, (_, n) => ({
      day: 20261020 + n,
      read_at: READ_AT,
      holdings_value_paise: 10_000_000 + n * 100_000,
      holdings_cost_paise: 9_000_000,
      positions_pnl_paise: n % 2 ? -1000 : 1000,
      provisional: n === 11,
      late: false,
    }))
    serve({}, { days, note: null })
    show('history')
    await screen.findByText('Last 10 days')
    const rows = within(screen.getByRole('list', { name: 'Last days' })).getAllByRole('listitem')
    expect(rows).toHaveLength(10)
    expect(within(rows[0]).getByText('31 Oct 2026')).toBeTruthy() // 20261031: newest first
    expect(within(rows[0]).getByText('Provisional')).toBeTruthy()
    expect(screen.getByText(/not real-time/)).toBeTruthy()
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/against/)
    expect(screen.getByRole('link', { name: /Portfolio Analyzer/ })).toBeTruthy()
  })

  it('says a part not read at the close was not recorded, never 0, and leaves the day off the chart', async () => {
    const day = (n: number, extra: object = {}) => ({
      day: 20261020 + n,
      read_at: READ_AT,
      holdings_value_paise: 10_000_000,
      holdings_cost_paise: 9_000_000,
      positions_pnl_paise: 1000,
      provisional: false,
      late: false,
      ...extra,
    })
    serve(
      {},
      {
        days: [
          day(0),
          day(1),
          day(2, { holdings_value_paise: null, holdings_cost_paise: null }),
          day(3, {
            positions_pnl_paise: null,
            not_read: ['Positions were not read at the close.'],
          }),
        ],
        note: null,
      }
    )
    show('history')
    const rows = within(await screen.findByRole('list', { name: 'Last days' })).getAllByRole(
      'listitem'
    )
    // Newest first: the 23rd lacks positions, the 22nd lacks holdings.
    expect(within(rows[0]).getByText('Positions were not read at the close.')).toBeTruthy()
    expect(within(rows[1]).getAllByText('Not recorded at the close')).toHaveLength(3)
    expect(within(rows[1]).queryByText(/₹0/)).toBeNull()
    expect(screen.getByText(/left out of the chart/)).toBeTruthy()
  })

  it('says so when there is no record yet', async () => {
    serve({}, { days: [], note: null })
    show('history')
    expect(await screen.findByText(/No closing records yet/)).toBeTruthy()
  })
})
