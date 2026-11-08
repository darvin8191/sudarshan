/**
 * The report views after the review of M7.6 and M7.7: "As of" carries the date, Read again reads
 * any year, a partial or missing report has its button, a failed re-ask is said in the bridge's
 * words and stops the asking, a read that is done stops the asking, mutual funds show their note
 * alone, and two rows with no trade number do not share a React key.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import { dateTimeOfInstant } from '../days'
import type { PnlReport, TradeRow, TradesReport } from '../types'
import { PnlView, READING_POLL_MS, TradesView } from './ReportsView'

const YEARS = {
  today: 20260502,
  years: [
    { fy: 2026, label: '2026-27', current: true },
    { fy: 2025, label: '2025-26', current: false },
    { fy: 2024, label: '2024-25', current: false },
  ],
}
const MARCH_31_1100_IST = Date.UTC(2026, 2, 31, 5, 30, 0)

function trades(extra: Partial<TradesReport> = {}): TradesReport {
  return {
    state: 'ok',
    read_at: MARCH_31_1100_IST,
    note: null,
    fy: 2026,
    segment: 'EQ',
    rows: [],
    total_records: 0,
    bought_paise: 0,
    sold_paise: 0,
    ...extra,
  }
}

function serve(answer: (op: string, args: Record<string, unknown>) => unknown) {
  api.call.mockImplementation(async (op: string, args: Record<string, unknown>) => {
    if (op === 'report_years') return { enabled: true, data: YEARS }
    const data = answer(op, args)
    if (data instanceof Error) throw data
    return { enabled: true, data }
  })
}

function show(view: 'trades' | 'pnl') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      {view === 'trades' ? <TradesView /> : <PnlView />}
    </QueryClientProvider>
  )
}

const asks = () => api.call.mock.calls.filter((c) => c[0] === 'report_trades').length

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (hidden ? 'hidden' : 'visible'),
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  api.call.mockReset()
  setHidden(false)
})
afterEach(() => {
  vi.useRealTimers()
  setHidden(false)
})

const READING = trades({ state: 'reading', read_at: null, note: 'Reading from Upstox.' })

describe('the date in "As of"', () => {
  it('names the day a record was read, not only the time', async () => {
    serve(() => trades({ fy: 2025 }))
    show('trades')
    expect((await screen.findByText(/^As of /)).textContent).toBe('As of 31 Mar 2026, 11:00:00')
  })

  it('writes an instant as day, month, year and the IST time', () => {
    expect(dateTimeOfInstant(MARCH_31_1100_IST)).toBe('31 Mar 2026, 11:00:00')
    expect(dateTimeOfInstant(Date.UTC(2026, 11, 31, 18, 29, 59))).toBe('31 Dec 2026, 23:59:59')
    expect(dateTimeOfInstant(Date.UTC(2026, 11, 31, 18, 30, 0))).toBe('1 Jan 2027, 00:00:00')
  })
})

describe('the reading re-ask', () => {
  it('stops asking once the service answers ok', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let n = 0
    serve(() => {
      n += 1
      return n < 3 ? READING : trades({ state: 'ok' })
    })
    show('trades')
    await screen.findByText('Reading from Upstox.')
    await act(() => vi.advanceTimersByTimeAsync(READING_POLL_MS * 3 + 100))
    await waitFor(() => expect(screen.queryByText('Reading from Upstox.')).toBeNull())
    const atOk = asks()
    expect(atOk).toBe(3)
    await act(() => vi.advanceTimersByTimeAsync(READING_POLL_MS * 10))
    expect(asks()).toBe(atOk)
  })

  it('says the service is down in its own words and stops asking when a re-ask fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let down = false
    const sentence =
      'The portfolio service is not answering. Your entries are safe; try again in a minute.'
    serve(() =>
      down ? new PortfolioError({ code: 'unavailable', message: sentence, entryId: null }) : READING
    )
    show('trades')
    await screen.findByText('Reading from Upstox.')
    down = true
    await act(() => vi.advanceTimersByTimeAsync(READING_POLL_MS + 100))
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(screen.queryByText('Reading from Upstox.')).toBeNull()
    const stopped = asks()
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(asks()).toBe(stopped)
  })
})

describe('the button of each state', () => {
  it('offers Read again on a complete year and sends force with the year and segment', async () => {
    let forced = 0
    serve((_op, args) => {
      if (args.force === true) {
        forced += 1
        return trades({ fy: 2026, read_at: Date.UTC(2026, 3, 4, 5, 0, 0), total_records: 0 })
      }
      return trades({ fy: 2026 })
    })
    show('trades')
    await screen.findByText(/^As of 31 Mar 2026/)
    await userEvent.click(await screen.findByRole('button', { name: 'Read again' }))
    expect(await screen.findByText('As of 4 Apr 2026, 10:30:00')).toBeInTheDocument()
    expect(forced).toBe(1)
    expect(api.call).toHaveBeenCalledWith('report_trades', { fy: 2026, segment: 'EQ', force: true })
  })

  it('offers Try again, and no Read again, when nothing could be read', async () => {
    serve(() => trades({ state: 'unavailable', read_at: null, note: 'Upstox did not answer.' }))
    show('trades')
    await screen.findByText('Upstox did not answer.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Read again' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(asks()).toBe(2))
  })

  it('offers Read more on a partial year and nothing while it is being read', async () => {
    serve(() => trades({ state: 'partial', note: 'More pages follow.' }))
    show('trades')
    expect(await screen.findByRole('button', { name: 'Read more' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Read again' })).toBeNull()
  })

  it('shows no button while a read is going on', async () => {
    serve(() => READING)
    show('trades')
    await screen.findByText('Reading from Upstox.')
    expect(screen.queryByRole('button', { name: /Read|Try/ })).toBeNull()
  })
})

describe('mutual funds in the P&L', () => {
  it('shows the service note alone, with nothing that is never going to be read', async () => {
    const mf: PnlReport = {
      state: 'ok',
      read_at: Date.UTC(2026, 4, 2, 4, 30),
      note: 'Upstox has no trade-wise P&L report for mutual funds; their trades are in Trades.',
      fy: 2026,
      segment: 'MF',
      rows: [],
      trades_count: null,
      realised_paise: 0,
      charges: null,
      net_paise: null,
    }
    serve(() => mf)
    show('pnl')
    await screen.findByText(/no trade-wise P&L report/)
    const text = document.body.textContent ?? ''
    for (const never of [
      'Upstox has not said how many trades yet.',
      'Not known until the charges are read.',
      'Charges have not been read from Upstox yet.',
      'As of',
      'are in History',
    ])
      expect(text).not.toContain(never)
  })
})

describe('rows with no trade number', () => {
  it('do not share a React key on one day', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const row: TradeRow = {
      trade_id: '',
      trade_date: 20260504,
      exchange: 'NSE',
      segment: 'EQ',
      transaction_type: 'BUY',
      scrip_name: 'ALPHA',
      symbol: 'ALPHA',
      isin: null,
      instrument_token: null,
      option_type: null,
      strike_price_u: null,
      expiry: null,
      qty_u: 1_000_000,
      price_u: 1_000_000,
      amount_paise: 10_000,
    }
    serve(() => trades({ rows: [row, { ...row, transaction_type: 'SELL' }], total_records: 2 }))
    show('trades')
    await screen.findAllByText('ALPHA')
    const duplicated = spy.mock.calls.some((c) => String(c[0]).includes('same key'))
    spy.mockRestore()
    expect(duplicated).toBe(false)
  })
})
