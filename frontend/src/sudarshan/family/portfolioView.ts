// SUDARSHAN P6 D6 (M7.41, M7.37): reading a shared portfolio through the viewer API, defensively.
//
// Assumed, since the contract types the two answers as `unknown`: the summary is the portfolio's
// own PortfolioSummary and a tab's view its own TabView (portfolio/types.ts), after the member's
// mask. As on the books (Y24) a field the mask removed is ABSENT and a null is not known yet:
// absent shows as Hidden, null as a dash, and neither is ever 0. Anything of a shape this reader
// does not know is left out, never guessed.
import { paiseToRupees } from './books'

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** A money field in paise as rupees: undefined when absent (hidden), null when not known. */
function rupeesAt(source: unknown, key: string): number | null | undefined {
  if (!isObject(source) || !(key in source)) return undefined
  const value = source[key]
  return typeof value === 'number' ? paiseToRupees(value) : null
}

export interface SharedFigures {
  value: number | null | undefined
  gain: number | null | undefined
  /** Today's change in rupees. */
  today: number | null | undefined
  /** Today's change as a share, as the service sent it. */
  todayPct: string | null | undefined
}

function todayOf(totals: Record<string, unknown>): Pick<SharedFigures, 'today' | 'todayPct'> {
  const figure = totals.today
  if (!isObject(figure)) return { today: undefined, todayPct: undefined }
  return {
    today: rupeesAt(figure, 'paise'),
    todayPct: 'pct' in figure ? (typeof figure.pct === 'string' ? figure.pct : null) : undefined,
  }
}

/** The totals of a live tab, or all of them hidden where the tab sends none. */
export function totalsOf(live: unknown): SharedFigures {
  const totals = isObject(live) && isObject(live.totals) ? live.totals : null
  if (!totals) return { value: undefined, gain: undefined, today: undefined, todayPct: undefined }
  return {
    value: rupeesAt(totals, 'value_paise'),
    gain: rupeesAt(totals, 'overall_gain_paise'),
    ...todayOf(totals),
  }
}

export interface SummaryLine {
  id: number | null
  name: string
  figures: SharedFigures
}

/** One line per tab of the summary. A tab with no live part is a line of hidden figures. */
export function summaryLines(summary: unknown): SummaryLine[] {
  if (!isObject(summary) || !Array.isArray(summary.entries)) return []
  const lines: SummaryLine[] = []
  for (const entry of summary.entries) {
    if (!isObject(entry) || !isObject(entry.tab) || typeof entry.tab.name !== 'string') continue
    lines.push({
      id: typeof entry.tab.id === 'number' ? entry.tab.id : null,
      name: entry.tab.name,
      figures: totalsOf(entry.live),
    })
  }
  return lines
}

/** A sum over lines: hidden when any is hidden, not known when any is not known. */
export function sumOf(
  lines: readonly SummaryLine[],
  pick: (figures: SharedFigures) => number | null | undefined
): number | null | undefined {
  let sum = 0
  for (const line of lines) {
    const value = pick(line.figures)
    if (value === undefined) return undefined
    if (value === null) return null
    sum += value
  }
  return sum
}

export interface TabHolding {
  key: string
  name: string
  symbol: string | null
}

export function holdingsOf(view: unknown): TabHolding[] {
  if (!isObject(view) || !Array.isArray(view.rows)) return []
  const out: TabHolding[] = []
  for (const row of view.rows) {
    if (!isObject(row) || !isObject(row.asset)) continue
    const { id, name, symbol } = row.asset
    if (typeof name !== 'string') continue
    out.push({
      key: String(id ?? name),
      name,
      symbol: typeof symbol === 'string' ? symbol : null,
    })
  }
  return out
}

export interface Allocation {
  label: string
  holdings: number
  value: number | null | undefined
  pct: string | null | undefined
}

/** The tab's allocation by class, as the service sent it (the first split it names). */
export function allocationOf(view: unknown): Allocation[] {
  if (!isObject(view) || !isObject(view.allocation)) return []
  const split = Object.values(view.allocation).find(Array.isArray)
  if (!split) return []
  const out: Allocation[] = []
  for (const row of split as unknown[]) {
    if (!isObject(row) || typeof row.label !== 'string') continue
    out.push({
      label: row.label,
      holdings: typeof row.holdings === 'number' ? row.holdings : 0,
      value: rupeesAt(row, 'value_paise'),
      pct: 'pct' in row ? (typeof row.pct === 'string' ? row.pct : null) : undefined,
    })
  }
  return out
}
