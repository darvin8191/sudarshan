/**
 * The Upstox tab's Allocation and Performance views (M7.15) in jsdom, the bridge a stand-in that
 * answers as the contract says. Pinned: the bars are worked out from the holdings and move with
 * a tick, "Real-time" shows only once ticks arrive, a holding with no class is said as not
 * classified (never guessed), the return says it is a closing record's and not real-time, a
 * share with no purchase in the history is listed as not counted, a read that fails or waits
 * says so in words with no zero in its place, and neither view has an order button.
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
  useSocketContext: () => ({
    playAlertSound: () => {},
    socket: { on: () => {}, off: () => {} },
  }),
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))

import { createQueryClient } from '@/app/providers'
import { useThemeStore } from '@/stores/themeStore'
import { feed } from '@/test/marketDataHarness'
import { PortfolioError } from '../api'
import type { UpstoxAllocation, UpstoxPerformance } from '../types'
import { unclassifiedWords } from './AllocationView'
import { holding, READ_AT } from './fixtures'
import { closeWords } from './PerformanceView'
import { UpstoxTab } from './UpstoxTab'

// INFY: 10 at 1,500 is 15,000. TCS: 5 at 1,000 is 5,000.
const INFY = holding()
const TCS = holding({
  instrument_key: 'NSE_EQ|INE000B00002',
  symbol: 'TCS',
  name: 'Tata Consultancy Services',
  isin: 'INE000B00002',
  qty_u: 5_000_000,
  avg_price_u: 9_000_000,
  last_price_u: 10_000_000,
  close_price_u: 10_000_000,
  pnl_paise: 50_000,
})
const BOOK = { holdings: { state: 'ok', read_at: READ_AT, note: null, data: [INFY, TCS] } }
const CLASSES: UpstoxAllocation = {
  state: 'ok',
  read_at: READ_AT,
  note: null,
  classes: [
    { instrument_key: INFY.instrument_key, asset_class: 'equity', sector: 'IT' },
    { instrument_key: TCS.instrument_key, asset_class: null, sector: null },
  ],
  allocation: { class: [], sector: [] },
}
const figure = { beta: null, correlation: null, excess_pct: null, stat_days: 0, stat_note: null }
const REPORT: UpstoxPerformance = {
  state: 'ok',
  note: null,
  from: 20240401,
  read_at: READ_AT,
  data: {
    valuation_day: 20261023,
    first_flow_day: 20251023,
    value_paise: 3_300_000,
    money_in_paise: 3_000_000,
    money_out_paise: 0,
    fee_paise: 0,
    net_put_in_paise: 3_000_000,
    total_gain_paise: 300_000,
    absolute_return_pct: '10.0000',
    xirr: { state: 'shown', pct: '10.0000', from_day: null, words: null },
    twr: {
      start_day: 20261022,
      pct: '-1.2500',
      annualised_pct: null,
      words: 'TWR since 22 Oct 2026',
    },
    benchmarks: [
      {
        benchmark: 'nifty50',
        label: 'NIFTY 50',
        value_paise: 3_150_000,
        xirr_pct: '5.0000',
        twr_pct: null,
        flagged: false,
        words: ['Dividends not included'],
        ...figure,
      },
    ],
  },
  uncounted: [
    {
      isin: 'INE000Z01011',
      symbol: 'SMALLCO',
      name: 'Small Company',
      qty_u: 3_000_000,
      value_paise: 27_000,
    },
  ],
  uncounted_value_paise: 27_000,
  words: ['Shares and ETFs only, from Upstox’s trade history since 1 Apr 2024.'],
}

function serve(over: Partial<Record<string, () => unknown>> = {}) {
  const answers: Record<string, () => unknown> = {
    upstox_book: () => BOOK,
    upstox_allocation: () => CLASSES,
    upstox_performance: () => REPORT,
    ...over,
  }
  api.call.mockImplementation(async (op: string) => {
    const answer = answers[op]
    if (!answer) throw new Error(`unexpected op ${op}`)
    return { enabled: true, data: answer() }
  })
}

function show(view: 'allocation' | 'performance') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <UpstoxTab view={view} onView={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const bars = (name: string) =>
  within(screen.getByRole('list', { name }))
    .getAllByRole('listitem')
    .map((li) => li.textContent)

beforeEach(() => {
  api.call.mockReset()
  feed.reset()
  useThemeStore.setState({ appMode: 'live' })
})

describe('the parts of the Upstox tab', () => {
  it('offers Allocation and Performance beside the six it had', async () => {
    serve()
    show('allocation')
    const names = screen.getAllByRole('tab').map((t) => t.textContent)
    expect(names).toEqual([
      'Holdings',
      'Positions',
      'Funds',
      'Allocation',
      'Performance',
      'History',
      'Trades',
      'P&L',
    ])
  })
})

describe('Allocation', () => {
  it('draws the bars by class from the holdings and says what is not classified', async () => {
    serve()
    show('allocation')
    await screen.findByRole('list', { name: 'Allocation by class' })
    expect(bars('Allocation by class')).toEqual([
      'Equity75.00%₹15,000.00 · 1 holding',
      'Not classified25.00%₹5,000.00 · 1 holding',
    ])
    // v0.16.0 phone review L5: a name is never cut ("E.." at 320 px with Larger text); where it
    // and its figures do not fit on one line, the figures go under it.
    const name = screen.getByText('Not classified')
    expect(name).not.toHaveClass('truncate')
    expect(name.parentElement).toHaveClass('flex-wrap')
    expect(screen.getByText('₹20,000.00')).toBeTruthy()
    expect(screen.getByText('Read 10:41:05')).toBeTruthy()
    expect(screen.getByText(unclassifiedWords(1))).toBeTruthy()
    // It reads the holdings as the Holdings view does, and the classes once.
    expect(api.call).toHaveBeenCalledWith('upstox_book', {
      parts: ['holdings'],
      max_age: undefined,
    })
    expect(api.call.mock.calls.filter(([op]) => op === 'upstox_allocation').length).toBe(1)
    await userEvent.click(screen.getByRole('button', { name: 'Sector' }))
    expect(bars('Allocation by sector')).toEqual([
      'IT75.00%₹15,000.00 · 1 holding',
      'No sector set25.00%₹5,000.00 · 1 holding',
    ])
    expect(screen.getByRole('button', { name: 'Sector' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('moves the bars with a tick, and says Real-time only once ticks arrive', async () => {
    serve()
    show('allocation')
    await screen.findByRole('list', { name: 'Allocation by class' })
    expect(screen.queryByText('Real-time')).toBeNull()
    act(() => feed.push('INFY', 'NSE', { ltp: 500 }))
    // INFY is now 10 at 500: 5,000 of 10,000, level with TCS; equal bars go by label.
    await waitFor(() =>
      expect(bars('Allocation by class')).toEqual([
        'Equity50.00%₹5,000.00 · 1 holding',
        'Not classified50.00%₹5,000.00 · 1 holding',
      ])
    )
    expect(screen.getByText('₹10,000.00')).toBeTruthy()
    expect(screen.getAllByText('Real-time').length).toBe(1)
  })

  it('is read-only: no order button anywhere', async () => {
    serve()
    show('allocation')
    await screen.findByRole('list', { name: 'Allocation by class' })
    expect(screen.queryByRole('button', { name: /buy|sell|exit|order/i })).toBeNull()
  })

  it('says so in words when the classes cannot be read, and draws no bar', async () => {
    const words = 'Your portfolio is not available just now. Try again in a minute.'
    serve({
      upstox_allocation: () => {
        throw new PortfolioError({ code: 'unavailable', message: words, entryId: null })
      },
    })
    show('allocation')
    expect(await screen.findByText(words)).toBeTruthy()
    expect(screen.queryByRole('list', { name: 'Allocation by class' })).toBeNull()
    expect(screen.queryByText(/Not classified/)).toBeNull() // unread is not unclassified
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('shows the service sentence, and no zero, when the holdings were not read', async () => {
    const words = 'Upstox did not answer.'
    serve({
      upstox_book: () => ({
        holdings: { state: 'unavailable', read_at: null, note: words, data: null },
      }),
    })
    show('allocation')
    expect(await screen.findByText(words)).toBeTruthy()
    expect(screen.queryByText(/₹0/)).toBeNull()
  })
})

describe('Performance', () => {
  it('shows the return as a closing record, with sign and arrow, and the index beside it', async () => {
    serve()
    show('performance')
    const section = await screen.findByRole('region', { name: 'Return' })
    expect(
      within(section).getByText(`${closeWords(20261023)} · trades since 1 Apr 2024`)
    ).toBeTruthy()
    expect(within(section).getByText('Closing record')).toBeTruthy()
    expect(within(section).getByText('+₹3,000.00')).toBeTruthy()
    expect(within(section).getAllByText('+10.00%').length).toBe(2) // the gain and the XIRR
    // A loss carries a true minus and an arrow, not colour alone.
    const twr = within(section).getByText('−1.25%')
    expect(twr.parentElement?.querySelector('svg') ?? twr.querySelector('svg')).toBeTruthy()
    expect(within(section).getByText('NIFTY 50')).toBeTruthy()
    expect(within(section).getByText('+5.00%')).toBeTruthy()
    expect(screen.getByText(REPORT.words[0])).toBeTruthy()
  })

  it('lists a share with no purchase in the history as not counted, never as cost nothing', async () => {
    serve()
    show('performance')
    const section = await screen.findByRole('region', { name: 'Not counted' })
    expect(within(section).getByText('SMALLCO')).toBeTruthy()
    expect(within(section).getByText('3 shares')).toBeTruthy()
    expect(
      within(section).getByText(/left out of the return, not taken as bought for nothing/)
    ).toBeTruthy()
  })

  it('moves only the value now with a tick, and marks it Real-time', async () => {
    serve()
    show('performance')
    const now = await screen.findByRole('region', { name: 'Holdings value now' })
    expect(within(now).getByText('₹20,000.00')).toBeTruthy()
    expect(screen.queryByText('Real-time')).toBeNull()
    act(() => feed.push('INFY', 'NSE', { ltp: 1600 }))
    await waitFor(() => expect(within(now).getByText('₹21,000.00')).toBeTruthy())
    expect(within(now).getByText('Real-time')).toBeTruthy()
    // The return is the closing record's: it did not move.
    expect(
      within(screen.getByRole('region', { name: 'Return' })).getByText('₹33,000.00')
    ).toBeTruthy()
  })

  it('waits in words, with no figure in their place', async () => {
    const words = 'Reading from Upstox. This takes a moment.'
    serve({
      upstox_performance: () => ({
        ...REPORT,
        state: 'waiting',
        note: words,
        data: null,
        uncounted: [],
        uncounted_value_paise: 0,
        words: [],
        from: null,
        read_at: null,
      }),
    })
    show('performance')
    expect(await screen.findByText(words)).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Return' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Not counted' })).toBeNull()
    expect(screen.queryByText(/%/)).toBeNull()
  })

  it('says a failed read in words and offers another try', async () => {
    const words = 'Your portfolio is not available just now. Try again in a minute.'
    let fail = true
    serve({
      upstox_performance: () => {
        if (fail) throw new PortfolioError({ code: 'unavailable', message: words, entryId: null })
        return REPORT
      },
    })
    show('performance')
    expect(await screen.findByText(words)).toBeTruthy()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Return' })).toBeTruthy()
  })
})
