// Typed from the documented field lists of the contract (types.ts), never captured from an account.
import type { UpstoxHolding, UpstoxPosition } from '../types'

/** 4 Nov 2026, 10:41:05 IST. */
export const READ_AT = Date.UTC(2026, 10, 4, 5, 11, 5)

export function holding(extra: Partial<UpstoxHolding> = {}): UpstoxHolding {
  return {
    instrument_key: 'NSE_EQ|INE000A00001',
    exchange: 'NSE',
    symbol: 'INFY',
    name: 'Infosys Ltd',
    isin: 'INE000A00001',
    qty_u: 10_000_000, // 10 shares
    t1_qty_u: 0,
    avg_price_u: 14_000_000, // 1,400
    last_price_u: 15_000_000, // 1,500 at the read
    close_price_u: 14_800_000, // 1,480
    pnl_paise: 100_000, // 1,000
    product: 'CNC',
    collateral_qty_u: 0,
    collateral_type: null,
    haircut_pct: null,
    cnc_used_qty_u: 0,
    cas: false,
    auction_from: null,
    auction_until: null,
    auction_close: null,
    ...extra,
  }
}

export function position(extra: Partial<UpstoxPosition> = {}): UpstoxPosition {
  return {
    instrument_key: 'NSE_FO|35003',
    exchange: 'NFO',
    symbol: 'NIFTY25NOV2625000CE',
    name: 'NIFTY 25000 CE',
    product: 'D',
    qty_u: 65_000_000, // 65
    overnight_qty_u: 0,
    day_buy_qty_u: 65_000_000,
    day_sell_qty_u: 0,
    avg_price_u: 1_000_000, // 100
    buy_price_u: 1_000_000,
    sell_price_u: 0,
    last_price_u: 1_100_000, // 110 at the read
    close_price_u: null,
    multiplier: 1,
    pnl_paise: 65_000, // 650
    realised_paise: 0,
    unrealised_paise: 65_000,
    value_paise: 0,
    cas: false,
    auction_from: null,
    auction_until: null,
    auction_close: null,
    ...extra,
  }
}
