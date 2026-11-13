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
