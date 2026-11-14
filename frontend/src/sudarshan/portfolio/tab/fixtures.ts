// Typed from the contract (types.ts), never captured from a real tab.
import type { HoldingRow, PerformanceReport, TabFixed, TabView } from '../types'

/** 4 Nov 2026, 10:41:05 IST. */
export const READ_AT = Date.UTC(2026, 10, 4, 5, 11, 5)
export const TODAY = 20261104

export function row(
  id: number,
  name: string,
  opts: {
    qty?: number
    price?: number | null
    prev?: number | null
    cost?: number
    kind?: 'listed' | 'mf' | 'manual'
    symbol?: string | null
    fresh?: HoldingRow['fresh']
    mode?: HoldingRow['today_mode']
    basis?: HoldingRow['price']['basis']
    day?: number | null
    at?: number | null
  } = {}
): HoldingRow {
  const kind = opts.kind ?? 'listed'
  const qty = opts.qty ?? 10_000_000 // 10 units
  return {
    asset: {
      id,
      kind,
      name,
      exchange: kind === 'listed' ? 'NSE' : null,
      symbol: kind === 'listed' ? (opts.symbol ?? name.toUpperCase()) : null,
      isin: null,
      unit_label: null,
      asset_class: 'equity',
      sector: null,
      mcap: null,
      tags: [],
    },
    price: {
      price_u: opts.price === undefined ? 1_000_000 : opts.price, // 100
      prev_close_u: opts.prev === undefined ? 990_000 : opts.prev, // 99
      day: opts.day === undefined ? TODAY : opts.day,
      at: opts.at === undefined ? READ_AT : opts.at,
      source: kind === 'listed' ? 'feed' : kind === 'mf' ? 'amfi' : 'manual',
      basis: opts.basis ?? (kind === 'listed' ? 'last' : kind === 'mf' ? 'nav' : 'manual'),
      late: false,
    },
    fresh: opts.fresh ?? { state: 'fresh', words: null },
    today_mode: opts.mode ?? (kind === 'listed' ? 'ok' : kind === 'mf' ? 'pending' : 'none'),
    cas: false,
    auction_from: null,
    auction_until: null,
    auction_close: null,
    lines: [
      {
        account_id: 1,
        qty_u: qty,
        open_qty_u: qty,
        cost_paise: opts.cost ?? 90_000, // 900
        in_today_paise: 0,
        out_today_paise: 0,
        over_year_qty_u: 0,
      },
    ],
  }
}

export const FIXED: TabFixed = {
  realised_paise: 0,
  income_paise: 0,
  fees_paise: 0,
  money_in_paise: 90_000,
  money_out_paise: 0,
  closed_today_paise: 0,
  closed_today_base_paise: 0,
  liabilities_paise: 0,
}

export function view(rows: HoldingRow[], extra: Partial<TabView> = {}): TabView {
  return {
    rows,
    fixed: FIXED,
    totals: {} as TabView['totals'],
    tab: {
      id: 2,
      name: 'Gold',
      name_alt: null,
      kind: 'stocks',
      icon: 'briefcase',
      colour: 'brand',
      position: 1,
      pinned: false,
      archived: false,
      system: false,
      manual_old_days: 30,
      version: 4,
    },
    today: TODAY,
    as_of: null,
    read_at: READ_AT,
    filters: { accounts: [], classes: [], tags: [], held: null, as_of: null },
    assets: [],
    liabilities: [],
    loans_hidden: false,
    loose_loans: [],
    allocation: {} as TabView['allocation'],
    notes: [],
    layout: {
      tab_id: 2,
      version: 1,
      spec: {
        v: 1,
        widgets: ['summary', 'today', 'holdings', 'movers'],
        columns: ['name', 'quantity', 'avg_cost', 'price', 'today', 'value', 'gain', 'weight'],
        sort: 'value',
        group: 'none',
      },
      defaults: {} as TabView['layout']['defaults'],
    },
    views: [],
    ...extra,
  }
}

/** A performance report of a tab a year and a half old, typed from the contract. */
export function report(extra: Partial<PerformanceReport> = {}): PerformanceReport {
  return {
    valuation_day: TODAY,
    first_flow_day: 20250501,
    value_paise: 12_000_000,
    money_in_paise: 10_000_000,
    money_out_paise: 0,
    fee_paise: 5_000,
    net_put_in_paise: 10_005_000,
    total_gain_paise: 1_995_000,
    absolute_return_pct: '19.9400',
    xirr: { state: 'shown', pct: '13.2000', from_day: null, words: null },
    twr: {
      start_day: 20250502,
      pct: '18.5000',
      annualised_pct: '12.1000',
      words: 'TWR since 2 May 2025',
    },
    benchmarks: [
      {
        benchmark: 'nifty50',
        label: 'NIFTY 50',
        value_paise: 11_000_000,
        xirr_pct: '9.1000',
        twr_pct: '12.0000',
        flagged: false,
        words: ['Dividends not included'],
        beta: '0.8500',
        correlation: '0.7200',
        excess_pct: '6.5000',
        stat_days: 60,
        stat_note: 'On 60 trading days to 13 Nov 2026',
      },
      {
        benchmark: 'nifty500',
        label: 'NIFTY 500',
        value_paise: null,
        xirr_pct: null,
        twr_pct: null,
        flagged: false,
        words: [
          'Dividends not included',
          'NIFTY 500 prices are not available yet; the comparison appears when they are.',
        ],
        beta: null,
        correlation: null,
        excess_pct: null,
        stat_days: 0,
        stat_note: 'Shown after 20 trading days (from 11 Dec 2026)',
      },
    ],
    series: [
      { day: 20261102, value_paise: 11_800_000, net_put_in_paise: 10_005_000 },
      { day: 20261103, value_paise: 11_900_000, net_put_in_paise: 10_005_000 },
    ],
    by_fy: [
      {
        fy: 2025,
        label: 'FY 2025-26',
        realised_paise: 250_000,
        income_paise: 40_000,
        fees_paise: 3_000,
      },
      {
        fy: 2026,
        label: 'FY 2026-27',
        realised_paise: -50_000,
        income_paise: 10_000,
        fees_paise: 2_000,
      },
    ],
    income: {
      this_fy_paise: 10_000,
      by_month: [
        { month: 202510, paise: 0 },
        { month: 202511, paise: 10_000 },
      ],
      last_12m_paise: 10_000,
      held_income_paise: 10_000,
      held_cost_paise: 800_000,
      yield_on_cost_pct: '1.2500',
      by_fy: [
        { fy: 2025, label: 'FY 2025-26', paise: 40_000 },
        { fy: 2026, label: 'FY 2026-27', paise: 10_000 },
      ],
    },
    series_words:
      'Daily history from 2 Nov 2026, the first day every holding here had a closing price.',
    ...extra,
  }
}
