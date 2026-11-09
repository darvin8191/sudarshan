/**
 * Upstox's trade history (M7.6) and trade-wise P&L with charges (M7.7) in jsdom. The bridge is a
 * stand-in answering with the README's shapes (paise, 1e-6 units, 1e-4 prices, days as 20261104).
 * Pinned: every state is a sentence and none shows a code; paise read as rupees; BUY and SELL as
 * words; a charge Upstox did not give reads "not given" and never 0; a partial year reads on when
 * asked; a read in progress asks again by itself; the year and segment go to the service as chosen.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import type { ChargesBreakdown, PnlReport, PnlRow, TradeRow, TradesReport } from '../types'
import { READ_AT } from './fixtures'
import { PART_WORDS, PnlView, READING_POLL_MS, TradesView } from './ReportsView'

const YEARS = {
  today: 20261108,
  years: [
    { fy: 2026, label: '2026-27', current: true },
    { fy: 2025, label: '2025-26', current: false },
    { fy: 2024, label: '2024-25', current: false },
  ],
}

function trade(extra: Partial<TradeRow> = {}): TradeRow {
  return {
    trade_id: 'T1',
    trade_date: 20261104,
    exchange: 'NSE',
    segment: 'EQ',
    transaction_type: 'BUY',
    scrip_name: 'INFOSYS LTD',
    symbol: 'INFY',
    isin: 'INE009A01021',
    instrument_token: null,
    option_type: null,
    strike_price_u: null,
    expiry: null,
    qty_u: 10_000_000, // 10 shares
    price_u: 14_000_000, // 1,400
    amount_paise: 1_400_000, // 14,000
    ...extra,
  }
}

function tradesReport(extra: Partial<TradesReport> = {}): TradesReport {
  return {
    state: 'ok',
    read_at: READ_AT,
    note: null,
    fy: 2026,
    segment: 'EQ',
    rows: [
      trade(),
      trade({
        trade_id: 'T2',
        trade_date: 20261105,
        transaction_type: 'SELL',
        scrip_name: 'TCS LTD',
        qty_u: 5_000_000,
        price_u: 41_234_500, // 4,123.45
        amount_paise: 2_061_725,
      }),
    ],
    total_records: 2,
    bought_paise: 1_400_000,
    sold_paise: 2_061_725,
    ...extra,
  }
}

const NO_CHARGES: ChargesBreakdown = {
  total_paise: null,
  brokerage_paise: null,
  gst_paise: null,
  stt_paise: null,
  stamp_duty_paise: null,
  transaction_paise: null,
  clearing_paise: null,
  ipft_paise: null,
  others_paise: null,
  sebi_turnover_paise: null,
  demat_transaction_paise: null,
}

function pnlRow(extra: Partial<PnlRow> = {}): PnlRow {
  return {
    scrip_name: 'RELIANCE INDUSTRIES',
    isin: 'INE002A01018',
    trade_type: 'EQ',
    qty_u: 2_000_000,
    buy_date: 20260410,
    sell_date: 20260920,
    buy_average_u: 25_000_000, // 2,500
    sell_average_u: 28_000_000, // 2,800
    buy_amount_paise: 500_000,
    sell_amount_paise: 560_000,
    pnl_paise: 60_000, // 600
    ...extra,
  }
}

function pnlReport(extra: Partial<PnlReport> = {}): PnlReport {
  return {
    state: 'ok',
    read_at: READ_AT,
    note: null,
    fy: 2026,
    segment: 'EQ',
    rows: [
      pnlRow(),
      pnlRow({ scrip_name: 'WIPRO LTD', isin: null, pnl_paise: -25_050, sell_date: null }),
    ],
    trades_count: 2,
    realised_paise: 34_950,
    charges: {
      ...NO_CHARGES,
      total_paise: 12_345,
      brokerage_paise: 0, // given as nil: a real zero
      gst_paise: 2_222,
      stt_paise: 10_123,
    },
    net_paise: 22_605,
    ...extra,
  }
}

/** The service as `serve({ report_trades: ..., report_pnl: ... })`; a function sees the args. */
function serve(answers: Record<string, unknown>) {
  api.call.mockImplementation(async (op: string, args: unknown) => {
    const answer = op === 'report_years' ? YEARS : answers[op]
    const data = typeof answer === 'function' ? (answer as (a: unknown) => unknown)(args) : answer
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

const callsOf = (op: string) => api.call.mock.calls.filter((c) => c[0] === op)

beforeEach(() => {
  api.call.mockReset()
})
afterEach(() => vi.useRealTimers())

describe('Trades', () => {
  it('says it is reading while the first answer is on its way', async () => {
    let release: (v: unknown) => void = () => {}
    api.call.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve
        })
    )
    show('trades')
    expect(await screen.findByText('Reading your trades from Upstox')).toBeInTheDocument()
    release({ enabled: true, data: YEARS })
  })

  it('shows the latest year first with each trade as words, rupees and the time it was read', async () => {
    serve({ report_trades: tradesReport() })
    show('trades')
    const list = await screen.findByRole('list', { name: 'Trades' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('INFOSYS LTD')).toBeInTheDocument()
    expect(within(rows[0]).getByText('BUY')).toBeInTheDocument()
    expect(within(rows[0]).getByText('10 at ₹1,400.00')).toBeInTheDocument()
    expect(within(rows[0]).getByText('₹14,000.00')).toBeInTheDocument()
    expect(within(rows[1]).getByText('SELL')).toBeInTheDocument()
    expect(within(rows[1]).getByText('5 at ₹4,123.45')).toBeInTheDocument()
    expect(within(rows[1]).getByText('₹20,617.25')).toBeInTheDocument()
    expect(screen.getByText('As of 4 Nov 2026, 10:41:05')).toBeInTheDocument()
    expect(callsOf('report_trades')[0][1]).toEqual({ fy: 2026, segment: 'EQ' })
  })

  it('carries the same rows in a table for wide screens', async () => {
    serve({ report_trades: tradesReport() })
    show('trades')
    const table = await screen.findByRole('table', { name: 'Trades table' })
    expect(within(table).getByRole('columnheader', { name: 'Quantity' })).toBeInTheDocument()
    const body = within(table).getAllByRole('row').slice(1)
    expect(within(body[1]).getByText('SELL')).toBeInTheDocument()
    expect(within(body[1]).getByText('₹4,123.45')).toBeInTheDocument()
    expect(within(body[1]).getByText('₹20,617.25')).toBeInTheDocument()
  })

  it('names the strike, side of the option and expiry of an F&O trade', async () => {
    serve({
      report_trades: tradesReport({
        segment: 'FO',
        rows: [
          trade({
            segment: 'FO',
            exchange: 'NFO',
            scrip_name: 'NIFTY 25000 CE',
            option_type: 'CE',
            strike_price_u: 250_000_000,
            expiry: 20261125,
          }),
        ],
      }),
    })
    show('trades')
    // the card and the table each carry it
    expect(await screen.findAllByText('₹25,000.00 CE, expiring 25 Nov 2026')).toHaveLength(2)
  })

  it('asks the service for the year and segment chosen', async () => {
    serve({ report_trades: tradesReport() })
    const user = userEvent.setup()
    show('trades')
    await screen.findByRole('list', { name: 'Trades' })
    await user.click(screen.getByRole('button', { name: '2025-26' }))
    await waitFor(() =>
      expect(callsOf('report_trades').at(-1)?.[1]).toEqual({ fy: 2025, segment: 'EQ' })
    )
    for (const [name, code] of [
      ['F&O', 'FO'],
      ['Commodity', 'COM'],
      ['Currency', 'CD'],
      ['Mutual funds', 'MF'],
    ]) {
      await user.click(screen.getByRole('button', { name }))
      await waitFor(() =>
        expect(callsOf('report_trades').at(-1)?.[1]).toEqual({ fy: 2025, segment: code })
      )
    }
    expect(screen.getByRole('button', { name: 'Mutual funds' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  it('says a year with no trades has none, in words', async () => {
    serve({
      report_trades: tradesReport({ rows: [], total_records: 0, bought_paise: 0, sold_paise: 0 }),
    })
    show('trades')
    expect(
      await screen.findByText('No trades in Equity in this financial year.')
    ).toBeInTheDocument()
  })

  it('says a read is in progress and asks again by itself until it is answered', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let n = 0
    serve({
      report_trades: () =>
        ++n === 1
          ? tradesReport({
              state: 'reading',
              rows: [],
              read_at: null,
              note: 'Reading from Upstox. This takes a moment.',
            })
          : tradesReport(),
    })
    show('trades')
    expect(await screen.findByText('Reading from Upstox. This takes a moment.')).toBeInTheDocument()
    expect(screen.getByText('Not read from Upstox yet')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(READING_POLL_MS + 100))
    expect(await screen.findByRole('list', { name: 'Trades' })).toBeInTheDocument()
    expect(callsOf('report_trades')).toHaveLength(2)
    expect(screen.queryByText('Reading from Upstox. This takes a moment.')).not.toBeInTheDocument()
  })

  it('falls back to its own sentence when a reading answer carries no note', async () => {
    serve({ report_trades: tradesReport({ state: 'reading', rows: [], note: null }) })
    show('trades')
    expect(await screen.findByText(PART_WORDS.reading)).toBeInTheDocument()
  })

  it('shows what is read of a partial year, says what to do, and reads on when asked', async () => {
    let n = 0
    serve({
      report_trades: () =>
        ++n === 1
          ? tradesReport({
              state: 'partial',
              note: 'More pages follow; the figures so far are shown.',
              total_records: 3,
            })
          : tradesReport({ total_records: 3, rows: [trade(), trade({ trade_id: 'T3' })] }),
    })
    const user = userEvent.setup()
    show('trades')
    expect(
      await screen.findByText('More pages follow; the figures so far are shown.')
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('list', { name: 'Trades' })).getAllByRole('listitem')
    ).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Read more' }))
    await waitFor(() =>
      expect(
        within(screen.getByRole('list', { name: 'Trades' })).getAllByRole('listitem')
      ).toHaveLength(2)
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Read more' })).toBeNull())
    expect(callsOf('report_trades')).toHaveLength(2)
  })

  it('tells a partial answer without a note what to do', async () => {
    serve({ report_trades: tradesReport({ state: 'partial', note: null }) })
    show('trades')
    expect(await screen.findByText(PART_WORDS.partial)).toBeInTheDocument()
  })

  it('says Upstox did not give the report, with no code, and nothing to show', async () => {
    serve({
      report_trades: tradesReport({ state: 'unavailable', rows: [], read_at: null, note: null }),
    })
    show('trades')
    expect(await screen.findByText(PART_WORDS.unavailable)).toBeInTheDocument()
    expect(screen.getByText('No trades to show yet.')).toBeInTheDocument()
    expect(screen.queryByText(/\b(4\d\d|5\d\d)\b/)).not.toBeInTheDocument()
  })

  it('keeps the service sentence when the bridge cannot answer, and offers another try', async () => {
    serve({ report_years: undefined })
    api.call.mockImplementation(async () => ({
      enabled: false,
      message: 'Portfolio is switched off on this server.',
    }))
    const user = userEvent.setup()
    show('trades')
    expect(await screen.findByText('Portfolio is switched off on this server.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(api.call.mock.calls.length).toBeGreaterThan(1))
  })
})

describe('P&L', () => {
  it('shows the totals, each closed trade with sign and arrow, and the time read', async () => {
    serve({ report_pnl: pnlReport() })
    show('pnl')
    const list = await screen.findByRole('list', { name: 'Closed trades' })
    const rows = within(list).getAllByRole('listitem')
    expect(within(rows[0]).getByText('RELIANCE INDUSTRIES')).toBeInTheDocument()
    expect(within(rows[0]).getByText(/\+₹600\.00/)).toBeInTheDocument()
    expect(within(rows[0]).getByText('Bought 10 Apr 2026 at ₹2,500.00')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Sold 20 Sep 2026 at ₹2,800.00')).toBeInTheDocument()
    expect(within(rows[1]).getByText(/−₹250\.50/)).toBeInTheDocument()
    expect(within(rows[1]).getByText('Sold on a day not given at ₹2,800.00')).toBeInTheDocument()
    const totals = screen.getByRole('region', { name: 'Realised P&L' })
    expect(within(totals).getByText(/\+₹349\.50/)).toBeInTheDocument()
    expect(within(totals).getByText(/\+₹226\.05/)).toBeInTheDocument()
    expect(within(totals).getByText('2 closed trades as Upstox pairs them.')).toBeInTheDocument()
    expect(within(totals).getByText('As of 4 Nov 2026, 10:41:05')).toBeInTheDocument()
  })

  it('has a table for wide screens with the same figures', async () => {
    serve({ report_pnl: pnlReport() })
    show('pnl')
    const table = await screen.findByRole('table', { name: 'Closed trades table' })
    const body = within(table).getAllByRole('row').slice(1)
    expect(within(body[0]).getByText('₹2,500.00')).toBeInTheDocument()
    expect(within(body[0]).getByText(/\+₹600\.00/)).toBeInTheDocument()
    expect(within(body[1]).getByText('Not given')).toBeInTheDocument()
  })

  it('lists every charge in rupees; a charge Upstox did not give reads not given, a real nil reads zero', async () => {
    serve({ report_pnl: pnlReport() })
    show('pnl')
    const charges = await screen.findByRole('group', { name: 'Charges' }).catch(() => null)
    const box = charges ?? (await screen.findByLabelText('Charges'))
    const line = (name: string) => within(box).getByText(name).parentElement as HTMLElement
    expect(within(line('Brokerage')).getByText('₹0.00')).toBeInTheDocument()
    expect(within(line('GST')).getByText('₹22.22')).toBeInTheDocument()
    expect(within(line('STT')).getByText('₹101.23')).toBeInTheDocument()
    expect(within(line('Total charges')).getByText('₹123.45')).toBeInTheDocument()
    for (const name of [
      'Stamp duty',
      'Exchange transaction charges',
      'Clearing charges',
      'SEBI turnover fee',
      'Investor protection fund',
      'Demat transaction charges',
      'Other charges',
    ]) {
      expect(within(line(name)).getByText('not given')).toBeInTheDocument()
    }
  })

  it('never shows an unread total or net as zero', async () => {
    serve({ report_pnl: pnlReport({ charges: { ...NO_CHARGES }, net_paise: null }) })
    show('pnl')
    const box = await screen.findByLabelText('Charges')
    expect(
      within(box.querySelector('div:last-child') as HTMLElement).getByText('not given')
    ).toBeInTheDocument()
    expect(screen.getByText('Not known until the charges are read.')).toBeInTheDocument()
    expect(within(box).queryByText('₹0.00')).not.toBeInTheDocument()
  })

  it('says charges are not read yet when there are none', async () => {
    serve({ report_pnl: pnlReport({ state: 'partial', charges: null, net_paise: null }) })
    show('pnl')
    expect(
      await screen.findByText('Charges have not been read from Upstox yet.')
    ).toBeInTheDocument()
    expect(screen.getByText(PART_WORDS.partial)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Read more' })).toBeInTheDocument()
  })

  it('says a year with no closed trades has none', async () => {
    serve({
      report_pnl: pnlReport({
        rows: [],
        trades_count: 0,
        realised_paise: 0,
        charges: null,
        net_paise: null,
      }),
    })
    show('pnl')
    expect(
      await screen.findByText('No closed trades in Equity in this financial year.')
    ).toBeInTheDocument()
    expect(screen.getByText('0 closed trades as Upstox pairs them.')).toBeInTheDocument()
  })

  it('shows the service sentence for a segment Upstox has no P&L for', async () => {
    serve({
      report_pnl: pnlReport({
        segment: 'MF',
        rows: [],
        trades_count: null,
        realised_paise: 0,
        charges: null,
        net_paise: null,
        note: 'Upstox has no trade-wise P&L report for mutual funds; their trades are in Trades.',
      }),
    })
    show('pnl')
    expect(
      await screen.findByText(/Upstox has no trade-wise P&L report for mutual funds/)
    ).toBeInTheDocument()
    expect(screen.queryByText('Upstox has not said how many trades yet.')).toBeNull()
  })

  it('says a read is in progress and a refused report in words', async () => {
    serve({
      report_pnl: pnlReport({
        state: 'reading',
        rows: [],
        note: null,
        charges: null,
        net_paise: null,
      }),
    })
    const first = show('pnl')
    expect(await screen.findByText(PART_WORDS.reading)).toBeInTheDocument()
    first.unmount()
    serve({
      report_pnl: pnlReport({
        state: 'unavailable',
        rows: [],
        read_at: null,
        note: 'Upstox did not accept this request.',
        charges: null,
        net_paise: null,
      }),
    })
    show('pnl')
    expect(await screen.findByText('Upstox did not accept this request.')).toBeInTheDocument()
    expect(screen.getByText('No closed trades to show yet.')).toBeInTheDocument()
  })

  it('asks for the year and segment chosen', async () => {
    serve({ report_pnl: pnlReport() })
    const user = userEvent.setup()
    show('pnl')
    await screen.findByRole('list', { name: 'Closed trades' })
    await user.click(screen.getByRole('button', { name: '2024-25' }))
    await user.click(screen.getByRole('button', { name: 'F&O' }))
    await waitFor(() =>
      expect(callsOf('report_pnl').at(-1)?.[1]).toEqual({ fy: 2024, segment: 'FO' })
    )
  })
})
