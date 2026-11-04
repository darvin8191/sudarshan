/**
 * The holdings list as the screen needs it (M7.23): one entry per held row with its figures at the
 * price on screen, the weight in the tab, the average cost, and the words for how fresh its price
 * is. Kept free of React. The sums are valuation.ts's, so a row here equals the service's row at
 * the same price.
 */
import { clockOfInstant } from '../days'
import { NO_PRICE } from '../freshness'
import { pct, roundDiv } from '../money'
import { notLiveWords } from '../statusWords'
import type { HoldingRow, Sort } from '../types'
import { isHeld, type RowFigures, rowFigures } from '../valuation'

export interface ListedRow {
  row: HoldingRow
  figures: RowFigures
  qtyU: number
  /** The average cost per unit as price_u, or null with nothing held or no cost. */
  avgCostU: number | null
  weightPct: string | null
  /** A tick moved this row's price. */
  moved: boolean
  /** The price's age in words, and whether it is Old (a clock icon goes beside the word). */
  words: string
  old: boolean
}

/** Cost per unit: cost_paise x 10^8 / qty_u gives price_u (rupees x 10^4). */
export function averageCostU(costPaise: number, qtyU: number): number | null {
  if (qtyU <= 0 || costPaise <= 0) return null
  return Number(roundDiv(BigInt(costPaise) * 100_000_000n, BigInt(qtyU)))
}

/** The words under a price: Real-time only for a row a tick moved, else its age. */
export function priceWords(row: HoldingRow, moved: boolean): { words: string; old: boolean } {
  if (row.price.price_u === null) return { words: NO_PRICE, old: false }
  if (moved) return { words: 'Real-time', old: false }
  if (row.asset.kind !== 'listed') {
    return { words: row.fresh.words ?? NO_PRICE, old: row.fresh.state === 'old' }
  }
  const taken = row.price.at === null ? null : clockOfInstant(row.price.at)
  return { words: row.fresh.words ?? notLiveWords(taken), old: row.fresh.state === 'old' }
}

export function listRows(
  rows: readonly HoldingRow[],
  moved: readonly boolean[],
  totalValuePaise: number
): ListedRow[] {
  const out: ListedRow[] = []
  rows.forEach((row, n) => {
    if (!isHeld(row)) return
    const figures = rowFigures(row)
    const qtyU = row.lines.reduce((sum, line) => sum + line.qty_u, 0)
    const { words, old } = priceWords(row, moved[n] === true)
    out.push({
      row,
      figures,
      qtyU,
      avgCostU: averageCostU(figures.cost_paise, qtyU),
      weightPct: totalValuePaise > 0 ? pct(figures.value_paise, totalValuePaise) : null,
      moved: moved[n] === true,
      words,
      old,
    })
  })
  return out
}

const num = (text: string | null): number =>
  text === null ? Number.NEGATIVE_INFINITY : Number(text)

/** The layout's sort. Unknown figures sort last, whichever way. */
export function sortRows(list: readonly ListedRow[], sort: Sort): ListedRow[] {
  const key: Record<Sort, (r: ListedRow) => number | string> = {
    value: (r) => r.figures.value_paise,
    today_pct: (r) => num(r.figures.today.pct),
    gain_pct: (r) => num(r.figures.gain_pct),
    weight: (r) => num(r.weightPct),
    name: (r) => r.row.asset.name.toLowerCase(),
  }
  const pick = key[sort]
  return [...list].sort((a, b) => {
    const x = pick(a)
    const y = pick(b)
    if (typeof x === 'string' && typeof y === 'string') return x.localeCompare(y)
    return (y as number) - (x as number)
  })
}

export interface Movers {
  best: ListedRow[]
  worst: ListedRow[]
  /** The largest moves in rupees, up or down. */
  contributors: ListedRow[]
}

/** Best 3 and worst 3 by Today's %, and the 3 largest Today contributions in rupees. */
export function moversOf(list: readonly ListedRow[], count = 3): Movers {
  const known = list.filter((r) => r.figures.today.state === 'ok' && r.figures.today.pct !== null)
  const byPct = [...known].sort((a, b) => Number(b.figures.today.pct) - Number(a.figures.today.pct))
  const best = byPct.filter((r) => Number(r.figures.today.pct) > 0).slice(0, count)
  const worst = [...byPct]
    .reverse()
    .filter((r) => Number(r.figures.today.pct) < 0)
    .slice(0, count)
  const contributors = [...known]
    .filter((r) => (r.figures.today.paise ?? 0) !== 0)
    .sort((a, b) => Math.abs(b.figures.today.paise ?? 0) - Math.abs(a.figures.today.paise ?? 0))
    .slice(0, count)
  return { best, worst, contributors }
}
