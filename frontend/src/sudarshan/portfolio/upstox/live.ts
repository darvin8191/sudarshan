/**
 * The Upstox tab's figures moved by ticks (M7.3), free of React so each rule is a plain function.
 *
 * Upstox's own figures are read at `read_at`; a tick newer than the read moves them, a read's
 * `last_price` is only the anchor (design 3.4, cross-design P18). Only a WebSocket tick counts:
 *
 * - Holding value = quantity x tick price.
 * - Holding P&L = Upstox `pnl` + quantity x (tick price - `last_price` at the read).
 * - Holding today = quantity x (tick price - `close_price`), Upstox's convention. No close: not
 *   known yet, never 0.
 * - Position P&L = Upstox `pnl` + net quantity x multiplier x (tick price - `last_price`). A
 *   short has a negative quantity, so a rising price lowers its P&L.
 */
import type { Tick } from '../liveTab'
import { feedPriceU, pct, valuePaise } from '../money'
import type { EpochMs, UpstoxHolding, UpstoxPosition } from '../types'

/** The price in `price_u` a tick gives, or null when it does not move the row. */
export function movedPriceU(tick: Tick | undefined, readAt: EpochMs | null): number | null {
  if (!tick || !tick.live || readAt === null || tick.at <= readAt) return null
  return feedPriceU(tick.ltp)
}

export interface HoldingNow {
  priceU: number
  moved: boolean
  valuePaise: number
  investedPaise: number
  pnlPaise: number
  pnlPct: string | null
  /** null: Upstox gave no previous close, so there is no figure (the screen says so). */
  todayPaise: number | null
  todayPct: string | null
}

export function holdingNow(
  row: UpstoxHolding,
  tick: Tick | undefined,
  readAt: EpochMs | null
): HoldingNow {
  const tickU = movedPriceU(tick, readAt)
  const priceU = tickU ?? row.last_price_u
  const invested = valuePaise(row.qty_u, row.avg_price_u)
  const pnl = row.pnl_paise + (tickU === null ? 0 : valuePaise(row.qty_u, tickU - row.last_price_u))
  const hasClose = row.close_price_u !== null && row.close_price_u > 0
  const closeValue = hasClose ? valuePaise(row.qty_u, row.close_price_u as number) : 0
  const today = hasClose ? valuePaise(row.qty_u, priceU) - closeValue : null
  return {
    priceU,
    moved: tickU !== null,
    valuePaise: valuePaise(row.qty_u, priceU),
    investedPaise: invested,
    pnlPaise: pnl,
    pnlPct: pct(pnl, invested),
    todayPaise: today,
    todayPct: today === null ? null : pct(today, closeValue),
  }
}

export interface HoldingsTotals {
  valuePaise: number
  investedPaise: number
  pnlPaise: number
  pnlPct: string | null
  /** The sum of the rows that have a figure; null when none has. */
  todayPaise: number | null
  /** Rows left out of Today for want of a previous close. */
  todayUnknown: number
}

export function holdingsTotals(rows: readonly HoldingNow[]): HoldingsTotals {
  let value = 0
  let invested = 0
  let pnl = 0
  let today = 0
  let counted = 0
  for (const row of rows) {
    value += row.valuePaise
    invested += row.investedPaise
    pnl += row.pnlPaise
    if (row.todayPaise !== null) {
      today += row.todayPaise
      counted += 1
    }
  }
  return {
    valuePaise: value,
    investedPaise: invested,
    pnlPaise: pnl,
    pnlPct: pct(pnl, invested),
    todayPaise: counted > 0 ? today : null,
    todayUnknown: rows.length - counted,
  }
}

export interface PositionNow {
  priceU: number
  moved: boolean
  pnlPaise: number
  unrealisedPaise: number
}

function times(paise: number, multiplier: number): number {
  const scaled = Math.abs(paise) * multiplier
  return paise < 0 ? -Math.round(scaled) : Math.round(scaled)
}

export function positionNow(
  row: UpstoxPosition,
  tick: Tick | undefined,
  readAt: EpochMs | null
): PositionNow {
  const tickU = movedPriceU(tick, readAt)
  const delta =
    tickU === null || row.qty_u === 0
      ? 0
      : times(valuePaise(row.qty_u, tickU - row.last_price_u), row.multiplier || 1)
  return {
    priceU: tickU ?? row.last_price_u,
    moved: tickU !== null,
    pnlPaise: row.pnl_paise + delta,
    unrealisedPaise: row.unrealised_paise + delta,
  }
}
