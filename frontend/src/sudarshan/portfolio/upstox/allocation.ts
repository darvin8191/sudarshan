/**
 * The Upstox holdings by class and by sector (M7.15), the phone's copy of
 * `core/allocation.allocate_book`, free of React.
 *
 * The service gives each holding the class and the sector the owner set on the same share in his
 * own tabs (`upstox_allocation`), and the bars at the prices of the read. Between reads the
 * phone works the same bars out again from the holdings as ticks move them, by the same rules: a
 * bar's share is its value over the whole, to four decimals, each on its own; largest first,
 * equal bars by label. A holding with no class or no sector is in a bar of its own that says so:
 * nothing is guessed. tests/vectors/upstox_parity.json runs against both languages.
 */
import { pct } from '../money'
import {
  type AllocationRow,
  ASSET_CLASS_LABELS,
  type AssetClass,
  type UpstoxHolding,
  type UpstoxHoldingClass,
} from '../types'

export const BOOK_BY = ['class', 'sector'] as const
export type BookBy = (typeof BOOK_BY)[number]

export const NOT_CLASSIFIED = 'Not classified'
export const NO_SECTOR = 'No sector set'

export interface BookLine {
  key: string
  value_paise: number
  asset_class: AssetClass | null
  sector: string | null
}

const byText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

export function bookBars(by: BookBy, lines: readonly BookLine[]): AllocationRow[] {
  let total = 0
  const bars = new Map<string, { label: string; value: number; held: Set<string> }>()
  for (const line of lines) {
    total += line.value_paise
    const known = by === 'class' ? line.asset_class : line.sector
    const key = known ?? ''
    const label =
      by === 'class'
        ? line.asset_class
          ? ASSET_CLASS_LABELS[line.asset_class]
          : NOT_CLASSIFIED
        : line.sector || NO_SECTOR
    const bar = bars.get(key) ?? { label, value: 0, held: new Set<string>() }
    bar.value += line.value_paise
    bar.held.add(line.key)
    bars.set(key, bar)
  }
  return [...bars.entries()]
    .map(([key, bar]) => ({
      key,
      label: bar.label,
      value_paise: bar.value,
      pct: (total ? pct(bar.value, total) : null) ?? '0.0000',
      holdings: bar.held.size,
    }))
    .sort(
      (a, b) => b.value_paise - a.value_paise || byText(a.label, b.label) || byText(a.key, b.key)
    )
}

/** Each holding at its value now, with the class and sector the service gave it. A holding the
 *  service has not classified yet (bought since its answer) is not classified here either. */
export function linesOf(
  rows: readonly Pick<UpstoxHolding, 'instrument_key'>[],
  values: readonly number[],
  classes: readonly UpstoxHoldingClass[]
): BookLine[] {
  const known = new Map(classes.map((c) => [c.instrument_key, c]))
  return rows.map((row, n) => {
    const found = known.get(row.instrument_key)
    return {
      key: row.instrument_key,
      value_paise: values[n] ?? 0,
      asset_class: found?.asset_class ?? null,
      sector: found?.sector || null,
    }
  })
}

/** How many holdings have no class or no sector. */
export function unclassified(lines: readonly BookLine[]): number {
  return lines.filter((l) => l.asset_class === null || l.sector === null).length
}
