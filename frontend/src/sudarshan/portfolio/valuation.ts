/**
 * Value, Today and a tab's totals from integers: the phone's copy of core/valuation.py (M7.24).
 *
 * The service works these sums out at its own prices; the phone works the same sums as ticks
 * arrive, and both run the vectors in sudarshan/portfolio/tests/vectors/valuation.json, so the two
 * cannot drift apart.
 *
 * - A line's value is round(qty_u x price_u / 10^8) paise, half away from zero (BigInt). Lines are
 *   rounded one by one and then added: every sum on a screen is a sum of figures on that screen.
 * - A line's Today is value now - (open value + in - out), the open value rounded on its own. It
 *   needs the previous close only when units were held at the open, and the price only when units
 *   are held now. A figure it needs and does not have is "not known yet" (state `unknown`, paise
 *   null), never 0.
 * - A holding with no price yet is counted at its cost, so one missing price does not look like a
 *   loss of everything in it; the tab says how many (`unpriced`).
 * - A holding sold out today is not in the list but its Today still counts, from `fixed`.
 */
import { pct, valuePaise } from './money'
import type { HoldingLine, PriceInfo, TabFixed, TabTotals, TodayFigure, TodayMode } from './types'

/** The part of a `HoldingRow` that the sums read. */
export interface ValuationRow {
  today_mode: TodayMode
  price: Pick<PriceInfo, 'price_u' | 'prev_close_u'>
  lines: readonly HoldingLine[]
}

/** The part of `TabFixed` that the sums read (liabilities do not enter them). */
export type ValuationFixed = Omit<TabFixed, 'liabilities_paise'> & { liabilities_paise?: number }

export interface RowFigures {
  value_paise: number
  cost_paise: number
  unrealised_paise: number
  /** Gain over cost; null with no price (the row is counted at cost) or no cost (a bonus lot). */
  gain_pct: string | null
  priced: boolean
  today: TodayFigure
}

const figure = (
  state: TodayFigure['state'],
  paise: number | null = null,
  base = 0,
  percent: string | null = null,
  counted = 0,
  unknown = 0,
  pending = 0
): TodayFigure => ({ state, paise, base_paise: base, pct: percent, counted, unknown, pending })

// ---- what the service decides before the sums ----------------------------------------------
/**
 * What Today means for a holding: a listed stock always has one; a mutual fund only once its NAV is
 * dated today (until AMFI publishes it, `pending`); a value kept by hand has none.
 */
export function todayMode(
  assetKind: string,
  basis: string | null,
  priceDay: number | null,
  today: number
): TodayMode {
  if (assetKind === 'listed') return 'ok'
  if (assetKind === 'mf' && basis === 'nav') return priceDay === today ? 'ok' : 'pending'
  return 'none'
}

/**
 * The close Today is measured from. A price from an earlier session is itself the latest close, so
 * Today is only today's trades.
 */
export function previousCloseU(
  priceU: number | null,
  prevCloseU: number | null,
  priceDay: number | null,
  today: number
): number | null {
  if (priceU !== null && priceDay !== null && priceDay < today) return priceU
  return prevCloseU
}

// ---- lines and rows ------------------------------------------------------------------------
/** `[Today, base]` of one line in paise; null when a price it needs is not known. */
export function lineToday(
  line: HoldingLine,
  priceU: number | null,
  prevCloseU: number | null
): [number, number] | null {
  if ((line.qty_u && priceU === null) || (line.open_qty_u && prevCloseU === null)) return null
  const now = line.qty_u && priceU !== null ? valuePaise(line.qty_u, priceU) : 0
  const opened =
    line.open_qty_u && prevCloseU !== null ? valuePaise(line.open_qty_u, prevCloseU) : 0
  const base = opened + line.in_today_paise
  return [now - base + line.out_today_paise, base]
}

/** Today for one holding: its lines added, or why there is no figure. */
export function rowToday(row: ValuationRow): TodayFigure {
  if (row.today_mode === 'none') return figure('none')
  if (row.today_mode === 'pending') return figure('pending', null, 0, null, 0, 0, 1)
  let paise = 0
  let base = 0
  for (const line of row.lines) {
    const got = lineToday(line, row.price.price_u, row.price.prev_close_u)
    if (got === null) return figure('unknown', null, 0, null, 0, 1)
    paise += got[0]
    base += got[1]
  }
  return figure('ok', paise, base, pct(paise, base), 1)
}

/** What one line is worth: its units at the price, or its cost while there is no price. */
export function lineValue(line: HoldingLine, priceU: number | null): number {
  return priceU === null ? line.cost_paise : valuePaise(line.qty_u, priceU)
}

/** Whether any account holds units now; a row that does not is hidden from the list. */
export const isHeld = (row: ValuationRow): boolean => row.lines.some((line) => line.qty_u !== 0)

export function rowFigures(row: ValuationRow): RowFigures {
  const cost = row.lines.reduce((sum, line) => sum + line.cost_paise, 0)
  const priced = row.price.price_u !== null
  const value = row.lines.reduce((sum, line) => sum + lineValue(line, row.price.price_u), 0)
  const gain = value - cost
  return {
    value_paise: value,
    cost_paise: cost,
    unrealised_paise: gain,
    gain_pct: priced ? pct(gain, cost) : null,
    priced,
    today: rowToday(row),
  }
}

// ---- the tab -------------------------------------------------------------------------------
/**
 * `[Today, base]` of the holdings sold out today, which the list no longer shows. The figure is
 * null when one of them has no previous close: a figure with a holding missing from it would be
 * wrong without saying so.
 */
export function closedToday(rows: readonly ValuationRow[]): [number | null, number] {
  let paise = 0
  let base = 0
  for (const row of rows) {
    if (isHeld(row)) continue
    const today = rowToday(row)
    if (today.state === 'unknown') return [null, 0]
    if (today.state === 'ok') {
      paise += today.paise ?? 0
      base += today.base_paise
    }
  }
  return [paise, base]
}

/** A tab's Today from its listed holdings' figures and the holdings sold out today. */
export function tabToday(todays: readonly TodayFigure[], fixed: ValuationFixed): TodayFigure {
  let paise = 0
  let base = 0
  let counted = 0
  let unknown = 0
  let pending = 0
  for (const today of todays) {
    if (today.state === 'ok') {
      paise += today.paise ?? 0
      base += today.base_paise
      counted += 1
    } else if (today.state === 'unknown') unknown += 1
    else if (today.state === 'pending') pending += 1
  }
  const closed = fixed.closed_today_paise
  const hasClosed = closed !== null && (closed !== 0 || fixed.closed_today_base_paise !== 0)
  if (hasClosed) {
    paise += closed
    base += fixed.closed_today_base_paise
  }
  let state: TodayFigure['state']
  if (counted || hasClosed) state = 'ok'
  else if (unknown || closed === null) state = 'unknown'
  else if (pending) state = 'pending'
  else state = 'none'
  if (state !== 'ok') return figure(state, null, 0, null, counted, unknown, pending)
  return figure('ok', paise, base, pct(paise, base), counted, unknown, pending)
}

/**
 * The tab's figures for these rows (the holdings in the list) and the sums beside them. A row
 * nobody holds is skipped, so passing every row is harmless.
 */
export function tabTotals(rows: readonly ValuationRow[], fixed: ValuationFixed): TabTotals {
  let value = 0
  let cost = 0
  let unpriced = 0
  const todays: TodayFigure[] = []
  for (const row of rows) {
    if (!isHeld(row)) continue
    const figures = rowFigures(row)
    value += figures.value_paise
    cost += figures.cost_paise
    if (!figures.priced) unpriced += 1
    todays.push(figures.today)
  }
  const unrealised = value - cost
  const overall = unrealised + fixed.realised_paise + fixed.income_paise - fixed.fees_paise
  const putIn = fixed.money_in_paise + fixed.fees_paise
  return {
    value_paise: value,
    cost_paise: cost,
    unrealised_paise: unrealised,
    overall_gain_paise: overall,
    absolute_return_pct: pct(value + fixed.money_out_paise - putIn, putIn),
    today: tabToday(todays, fixed),
    unpriced,
  }
}
