// SUDARSHAN P6 D5: the viewer's order and trade books as the service sends them, and the plain
// functions the read-only cards use, free of React so every rule is a call a test makes.
//
// What a row is. The service answers `rows: json[]` after the member's mask. These are assumed
// to be the portfolio's Upstox rows (portfolio/types.ts: UpstoxOrder, UpstoxTrade) with the
// mask's fields REMOVED, not zeroed: with quantities hidden an order has no `qty_u`,
// `filled_qty_u`, `pending_qty_u`, `price_u`, `trigger_price_u` or `avg_price_u` and a trade has
// no `qty_u`, `price_u` or `amount_paise`. A key that is absent is a hidden figure and reads
// "Hidden"; a key that is `null` is a figure the broker has not given and reads as a dash.
// Neither is ever 0. What the mask never removes: the instrument, the direction
// (`transaction_type`), the product, the order type, the status and the broker's words about
// it, and the times.
import { PRICE_SCALE, QTY_SCALE } from '@/sudarshan/portfolio/types'
import type { RowBase } from './books'
import type { Category } from './types'

export interface MaskedOrder extends RowBase {
  order_id: string
  product: string
  order_type?: string | null
  transaction_type: 'BUY' | 'SELL'
  status: string
  status_message?: string | null
  is_amo?: boolean
  qty_u?: number
  filled_qty_u?: number | null
  pending_qty_u?: number | null
  price_u?: number | null
  trigger_price_u?: number | null
  avg_price_u?: number | null
  placed_at?: number | null
  exchange_at?: number | null
}

export interface MaskedTrade extends RowBase {
  trade_id: string
  order_id?: string
  product: string
  order_type?: string | null
  transaction_type: 'BUY' | 'SELL'
  qty_u?: number
  price_u?: number
  amount_paise?: number
  placed_at?: number | null
  exchange_at?: number | null
}

/**
 * Which category hides each field the order and trade cards read. The one table is
 * sudarshan/family/privacy_fields.json; this is the phone's copy of the part these cards use,
 * and a test holds it to that file so the two cannot drift.
 */
export const ORDER_FIELD_CATEGORY: Readonly<Record<string, Category>> = {
  qty_u: 'qty',
  filled_qty_u: 'qty',
  pending_qty_u: 'qty',
  disclosed_qty_u: 'qty',
  price_u: 'qty',
  trigger_price_u: 'qty',
  avg_price_u: 'qty',
  amount_paise: 'qty',
}

/** Fields the mask never removes from an order or a trade. */
export const ORDER_NEVER_HIDDEN: readonly string[] = [
  'order_id',
  'trade_id',
  'symbol',
  'exchange',
  'product',
  'order_type',
  'transaction_type',
  'status',
  'status_message',
  'placed_at',
  'exchange_at',
  'instrument_key',
]

const quantity = (u: number | null | undefined): number | null | undefined =>
  u === undefined ? undefined : u === null || !Number.isFinite(u) ? null : u / QTY_SCALE
const rupees = (u: number | null | undefined): number | null | undefined =>
  u === undefined ? undefined : u === null || !Number.isFinite(u) ? null : u / PRICE_SCALE

/** The moment an order or a trade is shown at, newest first when sorted. */
export const momentOfRow = (row: { placed_at?: number | null; exchange_at?: number | null }) =>
  row.exchange_at ?? row.placed_at ?? 0

export interface OrderFigures {
  /** Ordered units; undefined: hidden. */
  total: number | null | undefined
  /** Filled units where something is known to have filled; null: not known; undefined: hidden. */
  filled: number | null | undefined
  pending: number | null | undefined
  price: number | null | undefined
  trigger: number | null | undefined
  average: number | null | undefined
}

export function orderFigures(row: MaskedOrder): OrderFigures {
  return {
    total: quantity(row.qty_u),
    filled: quantity(row.filled_qty_u),
    pending: quantity(row.pending_qty_u),
    price: rupees(row.price_u),
    trigger: rupees(row.trigger_price_u),
    average: rupees(row.avg_price_u),
  }
}

export interface TradeFigures {
  quantity: number | null | undefined
  price: number | null | undefined
  value: number | null | undefined
}

export function tradeFigures(row: MaskedTrade): TradeFigures {
  return {
    quantity: quantity(row.qty_u),
    price: rupees(row.price_u),
    value:
      row.amount_paise === undefined
        ? undefined
        : Number.isFinite(row.amount_paise)
          ? row.amount_paise / 100
          : null,
  }
}

/** The type as the book spells it, upper case, or ''. */
export const typeOf = (row: { order_type?: string | null }) =>
  String(row.order_type ?? '')
    .trim()
    .toUpperCase()
