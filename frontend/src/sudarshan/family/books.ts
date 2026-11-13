// SUDARSHAN P6 D4: the viewer's books as the service sends them, and the arithmetic that moves
// them with ticks, free of React so every rule is a plain function a test calls.
//
// What a row is. The service answers `rows: json[]` after the member's mask (contract.py: "the
// lanes that serve them narrow them"). The rows are assumed to be the portfolio's Upstox book
// rows (portfolio/types.ts: UpstoxPosition, UpstoxHolding), with the mask's fields REMOVED, not
// zeroed. That is the whole of Y24's rule on this side: a key that is absent (`undefined`) is a
// hidden figure and is shown as "Hidden"; a key that is `null` is a figure not known yet and is
// shown as a dash; neither is ever shown as 0.
//
// The one thing the mask must leave and the portfolio rows do not carry once `qty_u` is gone is
// the direction of a position. It is assumed to arrive as `side` ('long', 'short' or 'closed'),
// and where `qty_u` is present its sign decides, so a wrong `side` can never contradict it.
import { feedPriceU, valuePaise } from '@/sudarshan/portfolio/money'
import { PRICE_SCALE, QTY_SCALE } from '@/sudarshan/portfolio/types'
import type { Book, Category } from './types'

/** The tick as the portfolio's live code reads it (liveTab.ts `Tick`). */
export interface RowTick {
  ltp: number
  prev_close: number | null
  at: number
  live: boolean
}

export type Side = 'long' | 'short' | 'closed'

export interface RowBase {
  instrument_key?: string
  exchange: string | null
  symbol: string | null
  name?: string
  product?: string
  last_price_u?: number | null
  close_price_u?: number | null
}

/** A position row after the mask. A field the mask removed is absent. */
export interface MaskedPosition extends RowBase {
  product: string
  side?: Side
  qty_u?: number
  overnight_qty_u?: number
  day_buy_qty_u?: number
  day_sell_qty_u?: number
  avg_price_u?: number
  buy_price_u?: number
  sell_price_u?: number
  multiplier?: number
  pnl_paise?: number
  realised_paise?: number
  unrealised_paise?: number
  value_paise?: number
}

/** A holding row after the mask. */
export interface MaskedHolding extends RowBase {
  isin?: string | null
  qty_u?: number
  t1_qty_u?: number
  avg_price_u?: number
  pnl_paise?: number
  product?: string
}

/**
 * Which category hides each field a card reads. The privacy table itself is
 * sudarshan/family/privacy_fields.json, one table for every reader (Y24); this is the phone's
 * copy of the part the cards use, and a test holds it to that file so the two cannot drift.
 */
export const FIELD_CATEGORY: Readonly<Record<string, Category>> = {
  pnl_paise: 'pnl',
  realised_paise: 'pnl',
  unrealised_paise: 'pnl',
  qty_u: 'qty',
  overnight_qty_u: 'qty',
  day_buy_qty_u: 'qty',
  day_sell_qty_u: 'qty',
  t1_qty_u: 'qty',
  avg_price_u: 'qty',
  buy_price_u: 'qty',
  sell_price_u: 'qty',
  value_paise: 'qty',
}

/** Fields the mask never removes: the cards depend on them for a child's screen. */
export const NEVER_HIDDEN: readonly string[] = [
  'symbol',
  'exchange',
  'product',
  'name',
  'last_price_u',
  'close_price_u',
  'multiplier',
]

/** The rows of a book answer that are rows (a malformed entry is left out, not shown as zero). */
export function rowsOf<T extends RowBase>(book: Book | undefined): T[] {
  if (!book || !Array.isArray(book.rows)) return []
  return book.rows.filter(
    (row): row is T =>
      typeof row === 'object' && row !== null && 'exchange' in row && 'symbol' in row
  )
}

/** Whether a row names an instrument the feed can price. */
export function instrumentOf(row: RowBase): { symbol: string; exchange: string } | null {
  return row.symbol && row.exchange ? { symbol: row.symbol, exchange: row.exchange } : null
}

const rupees = (priceU: number | null | undefined): number | null =>
  typeof priceU === 'number' && Number.isFinite(priceU) ? priceU / PRICE_SCALE : null

/** A figure in paise as rupees, or null where it is absent or null. */
export const paiseToRupees = (paise: number | null | undefined): number | null =>
  typeof paise === 'number' && Number.isFinite(paise) ? paise / 100 : null

/** The price in `price_u` a live tick newer than the read gives, or null. */
export function movedPriceU(tick: RowTick | undefined, readAt: number | null): number | null {
  if (!tick || !tick.live || readAt === null || tick.at <= readAt) return null
  return feedPriceU(tick.ltp)
}

/** The direction of a position: the sign of its quantity where known, else the service's `side`. */
export function sideOf(row: Pick<MaskedPosition, 'qty_u' | 'side'>): Side | null {
  if (typeof row.qty_u === 'number')
    return row.qty_u > 0 ? 'long' : row.qty_u < 0 ? 'short' : 'closed'
  return row.side ?? null
}

/** What a quantity-free row can still say: an open row has a side that is not 'closed'. */
export function isOpen(row: Pick<MaskedPosition, 'qty_u' | 'side'>): boolean {
  const side = sideOf(row)
  return side === 'long' || side === 'short'
}

function times(paise: number, multiplier: number): number {
  const scaled = Math.abs(paise) * multiplier
  return paise < 0 ? -Math.round(scaled) : Math.round(scaled)
}

/** The previous close in rupees: a tick's own when it carries one, else the book's. */
function prevCloseOf(row: RowBase, tick: RowTick | undefined): number | null {
  const fromTick = tick?.prev_close
  if (typeof fromTick === 'number' && fromTick > 0) return fromTick
  const fromBook = rupees(row.close_price_u)
  return fromBook !== null && fromBook > 0 ? fromBook : null
}

/** The price and the day's change, which no mask removes: the child's whole row. */
export interface PriceFigures {
  /** Last price in rupees; null before any price is known. */
  ltp: number | null
  /** A newer live tick brought it. */
  moved: boolean
  /** Change since the previous close as a percentage; null with no previous close. */
  dayPercent: number | null
}

export function priceFigures(
  row: RowBase,
  tick: RowTick | undefined,
  readAt: number | null
): PriceFigures {
  const tickU = movedPriceU(tick, readAt)
  const ltp = tickU !== null ? tickU / PRICE_SCALE : rupees(row.last_price_u)
  const prev = prevCloseOf(row, tick)
  const dayPercent = ltp !== null && prev !== null ? ((ltp - prev) / prev) * 100 : null
  return { ltp: ltp !== null && ltp > 0 ? ltp : null, moved: tickU !== null, dayPercent }
}

export interface PositionFigures extends PriceFigures {
  side: Side | null
  /** Signed units; undefined: hidden. */
  quantity: number | undefined
  average: number | null | undefined
  /** Rupees; undefined: hidden. */
  pnl: number | null | undefined
  /** P&L as a share of what the position cost; undefined: hidden or not worked out. */
  pnlPercent: number | null | undefined
  realised: number | null | undefined
  unrealised: number | null | undefined
}

/**
 * A position moved by a tick: the service's P&L plus the move since the read, as the owner's
 * Positions page does it (a short has a negative quantity, so a rising price lowers it). Where
 * the quantity is hidden, so is the P&L (Y24), and nothing moves but the price.
 */
export function positionFigures(
  row: MaskedPosition,
  tick: RowTick | undefined,
  readAt: number | null
): PositionFigures {
  const price = priceFigures(row, tick, readAt)
  const tickU = movedPriceU(tick, readAt)
  const qtyU = row.qty_u
  const anchor = row.last_price_u
  let delta = 0
  if (tickU !== null && typeof qtyU === 'number' && qtyU !== 0 && typeof anchor === 'number') {
    delta = times(valuePaise(qtyU, tickU - anchor), row.multiplier || 1)
  }
  const shift = (paise: number | undefined) => (paise === undefined ? undefined : paise + delta)
  const pnlPaise = shift(row.pnl_paise)
  let pnlPercent: number | null | undefined
  if (pnlPaise === undefined) pnlPercent = undefined
  else if (typeof qtyU === 'number' && typeof row.avg_price_u === 'number' && qtyU !== 0) {
    const cost = times(valuePaise(Math.abs(qtyU), row.avg_price_u), row.multiplier || 1)
    pnlPercent = cost > 0 ? (pnlPaise / cost) * 100 : null
  } else pnlPercent = null
  return {
    ...price,
    side: sideOf(row),
    quantity: typeof qtyU === 'number' ? qtyU / QTY_SCALE : undefined,
    average: row.avg_price_u === undefined ? undefined : rupees(row.avg_price_u),
    pnl: pnlPaise === undefined ? undefined : paiseToRupees(pnlPaise),
    pnlPercent,
    realised: row.realised_paise === undefined ? undefined : paiseToRupees(row.realised_paise),
    unrealised:
      row.unrealised_paise === undefined ? undefined : paiseToRupees(row.unrealised_paise + delta),
  }
}

export interface HoldingFigures extends PriceFigures {
  quantity: number | undefined
  average: number | null | undefined
  /** What it is worth now. */
  value: number | null | undefined
  /** What went into it. */
  invested: number | null | undefined
  pnl: number | null | undefined
  pnlPercent: number | null | undefined
  /** The change since the previous close in rupees; null with no previous close. */
  today: number | null | undefined
}

/** A holding moved by a tick: value, P&L and today's change at the tick's price. */
export function holdingFigures(
  row: MaskedHolding,
  tick: RowTick | undefined,
  readAt: number | null
): HoldingFigures {
  const price = priceFigures(row, tick, readAt)
  const tickU = movedPriceU(tick, readAt)
  const priceU = tickU ?? (typeof row.last_price_u === 'number' ? row.last_price_u : null)
  const qtyU = row.qty_u
  const hasQty = typeof qtyU === 'number'
  const value = !hasQty
    ? undefined
    : priceU === null
      ? null
      : paiseToRupees(valuePaise(qtyU, priceU))
  const invested =
    !hasQty || row.avg_price_u === undefined
      ? undefined
      : paiseToRupees(valuePaise(qtyU, row.avg_price_u))
  let pnl: number | null | undefined
  if (row.pnl_paise === undefined) pnl = undefined
  else {
    const move =
      tickU !== null && hasQty && typeof row.last_price_u === 'number'
        ? valuePaise(qtyU, tickU - row.last_price_u)
        : 0
    pnl = paiseToRupees(row.pnl_paise + move)
  }
  const pnlPercent =
    pnl === undefined
      ? undefined
      : pnl === null || typeof invested !== 'number' || invested <= 0
        ? null
        : (pnl / invested) * 100
  let today: number | null | undefined
  const close = prevCloseOf(row, tick)
  if (!hasQty) today = undefined
  else if (close === null || price.ltp === null) today = null
  else today = (price.ltp - close) * (qtyU / QTY_SCALE)
  return {
    ...price,
    quantity: hasQty ? qtyU / QTY_SCALE : undefined,
    average: row.avg_price_u === undefined ? undefined : rupees(row.avg_price_u),
    value,
    invested,
    pnl,
    pnlPercent,
    today,
  }
}

/**
 * A total over rows: undefined (hidden) when any row's figure is hidden, null when a row's is
 * not known yet, else the sum. A sum that skipped a hidden row would read as a smaller number.
 */
export function totalOf(
  values: ReadonlyArray<number | null | undefined>
): number | null | undefined {
  let sum = 0
  for (const value of values) {
    if (value === undefined) return undefined
    if (value === null) return null
    sum += value
  }
  return sum
}
