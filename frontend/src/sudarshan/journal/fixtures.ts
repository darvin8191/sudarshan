import type { Trip } from './types'

/** A synthetic trade: money in paise, prices in price_u (rupees x 10^4). */
export function trip(over: Partial<Trip> & { id: number }): Trip {
  return {
    mode: 'live',
    exchange: 'NSE',
    symbol: 'SBIN',
    instrument_key: 'NSE_EQ|TEST0000001',
    product: 'MIS',
    product_class: 'intraday',
    side: 'long',
    qty_max: 65,
    entry_qty: 65,
    exit_qty: 0,
    entry_avg_u: 1_000_000,
    exit_avg_u: null,
    opened_ms: Date.UTC(2026, 10, 13, 4, 0),
    closed_ms: null,
    open_day: 20261113,
    close_day: null,
    status: 'open',
    gross_paise: null,
    charges_est_paise: null,
    charges_actual_paise: null,
    net_paise: null,
    hold_s: null,
    strategy: '',
    strategy_override: null,
    setup_id: null,
    own_expiry_day: false,
    index_expiry_day: false,
    risk_paise: null,
    mae_paise: null,
    mfe_paise: null,
    mae_ms: null,
    mfe_ms: null,
    excursion_source: 'none',
    excursion_note: null,
    version: 1,
    charges_modelled: true,
    ...over,
  }
}

/** A closed trade of `net` paise on `day`, closed at `closedMs`. */
export function closedTrip(
  over: Partial<Trip> & { id: number; net_paise: number; close_day: number }
): Trip {
  const closedMs = over.closed_ms ?? Date.UTC(2026, 10, 13, 5, 0)
  return trip({
    status: 'closed',
    exit_qty: 65,
    exit_avg_u: 1_010_000,
    closed_ms: closedMs,
    open_day: over.close_day,
    hold_s: 900,
    gross_paise: over.net_paise + 4049,
    charges_est_paise: 4049,
    ...over,
  })
}

import type { Analytics, Metrics } from './types'

/** Vector M1 (design 5.2): nets +812.50, -325.00, +260.00, 0.00, +650.00, -1,000.00; risk 500.00 each. */
export function metrics(over: Partial<Metrics> = {}): Metrics {
  return {
    trips: 6,
    wins: 3,
    losses: 2,
    flats: 1,
    net_paise: 39750,
    gross_paise: 46500,
    charges_paise: 6750,
    win_rate: '60.00',
    win_rate_state: 'ok',
    profit_factor: '1.30',
    profit_factor_state: 'ok',
    expectancy_paise: 6625,
    avg_win_paise: 57417,
    avg_loss_paise: -66250,
    largest_win_paise: 81250,
    largest_loss_paise: -100000,
    payoff: '0.87',
    payoff_state: 'ok',
    r_known: 6,
    r_total: '0.7950',
    r_avg: '0.1325',
    charges_impact_pct: '3.92',
    charges_estimated: true,
    charges_not_estimated: 0,
    ...over,
  }
}

/** A month of closed trades as the analytics op answers them (synthetic). */
export function analytics(over: Partial<Analytics> = {}): Analytics {
  const m = metrics()
  return {
    metrics: m,
    hold: [
      { bucket: 'under_5m', trips: 2, wins: 1, net_paise: 20000 },
      { bucket: '5_30m', trips: 3, wins: 2, net_paise: 40000 },
      { bucket: '30m_2h', trips: 1, wins: 0, net_paise: -20250 },
      { bucket: 'over_2h', trips: 0, wins: 0, net_paise: 0 },
      { bucket: '1_5d', trips: 0, wins: 0, net_paise: 0 },
      { bucket: 'over_5d', trips: 0, wins: 0, net_paise: 0 },
    ],
    calendar: {
      days: [
        { key: '20261110', trips: 2, wins: 1, net_paise: 81250 - 32500 },
        { key: '20261112', trips: 1, wins: 0, net_paise: -100000 },
        { key: '20261113', trips: 3, wins: 2, net_paise: 26000 + 65000 },
      ],
      weeks: [{ key: '2026-W46', trips: 6, wins: 3, net_paise: 39750 - 0 }],
      months: [{ key: '2026-11', trips: 6, wins: 3, net_paise: 39750 }],
    },
    equity: {
      points: [
        {
          day: 20261110,
          net_paise: 48750,
          equity_paise: 48750,
          peak_paise: 48750,
          drawdown_paise: 0,
        },
        {
          day: 20261112,
          net_paise: -100000,
          equity_paise: -51250,
          peak_paise: 48750,
          drawdown_paise: -100000,
        },
        {
          day: 20261113,
          net_paise: 91000,
          equity_paise: 39750,
          peak_paise: 48750,
          drawdown_paise: -9000,
        },
      ],
      drawdown: {
        max_drawdown_paise: -100000,
        max_drawdown_pct: '-2.00',
        peak_day: 20261110,
        trough_day: 20261112,
        recovered_day: null,
        duration_days: null,
        duration_state: 'still_in_drawdown',
      },
    },
    heat: [
      { weekday: 1, hour: 9, trips: 2, net_paise: 48750 },
      { weekday: 3, hour: 10, trips: 1, net_paise: -100000 },
      { weekday: 4, hour: 14, trips: 3, net_paise: 91000 },
    ],
    by_strategy: [
      { name: 'ORB', metrics: metrics({ trips: 4, net_paise: 140000, win_rate: '75.00' }) },
      { name: 'Gap fade', metrics: metrics({ trips: 2, net_paise: -100250, win_rate: '0.00' }) },
    ],
    by_tag: [],
    by_playbook: [],
    expiry: {
      index_expiry: metrics({ trips: 2, net_paise: -20250 }),
      not_index_expiry: metrics({ trips: 4, net_paise: 60000 }),
      own_expiry: metrics({ trips: 1, net_paise: -100000 }),
      not_own_expiry: metrics({ trips: 5, net_paise: 139750 }),
    },
    beta: null,
    ...over,
  }
}
