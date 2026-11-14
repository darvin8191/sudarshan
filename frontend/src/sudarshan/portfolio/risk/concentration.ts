// SUDARSHAN P7 lane G (M7.32): the phone's copy of the risk core's concentration rule
// (sudarshan/portfolio/core/risk.py `concentration`, and its two sentences in core/words.py), so
// the bars can be worked out again each time a tick moves a price. The service sends the rows its
// own bars were worked out from (`RiskReport.live`); `useTabLive` prices them; this file does the
// rest. Every sum is a whole number of paise and every division a BigInt `roundDiv`, as in the
// service. vectors/risk.json (`concentration` and `rows`) runs against both, so the two cannot
// drift: a change of the rule there fails here until this copy follows.
import { formatPct, pct, roundDiv } from '../money'
import {
  type AssetRef,
  HOLDING_KINDS,
  type HoldingLine,
  PCT_DECIMALS,
  type PriceInfo,
  type RiskConcentration,
  type RiskWeight,
} from '../types'
import { lineValue } from '../valuation'

/** How many bars each list shows; a share over its limit is always listed. */
export const TOP = 5
const SCALE = 10n ** BigInt(PCT_DECIMALS)

/** One instrument as a tab holds it: `key` makes it the same instrument in another tab. */
export interface Holding {
  key: string
  label: string
  value_paise: number
  sector: string | null
}

/** The part of a `HoldingRow` the rule reads. */
export interface RiskRow {
  asset: Pick<AssetRef, 'id' | 'name' | 'kind' | 'sector'>
  price: Pick<PriceInfo, 'price_u'>
  lines: readonly Pick<HoldingLine, 'qty_u' | 'cost_paise'>[]
}

/** What each row is worth now: its units at the price, or its cost while there is no price. */
export function holdingsOf(rows: readonly RiskRow[]): Holding[] {
  const kinds: readonly string[] = HOLDING_KINDS
  return rows
    .filter((row) => kinds.includes(row.asset.kind))
    .map((row) => ({
      key: String(row.asset.id),
      label: row.asset.name,
      value_paise: row.lines.reduce(
        (sum, line) => sum + lineValue(line as HoldingLine, row.price.price_u),
        0
      ),
      sector: row.asset.sector,
    }))
}

/** A percentage text of up to four decimals as ten-thousandths of a percent: "12.5" -> 125000n. */
function scaled(text: string): bigint {
  const [whole, part = ''] = text.split('.')
  return BigInt(whole) * SCALE + BigInt(part.padEnd(PCT_DECIMALS, '0').slice(0, PCT_DECIMALS))
}

/** A figure of four decimals as a sentence says it: to two, half up; a whole one stays whole. */
export function two(pctText: string): string {
  const hundredths = roundDiv(scaled(pctText.replace('-', '')), 100n)
  const sign = pctText.startsWith('-') && hundredths !== 0n ? '-' : ''
  if (hundredths % 100n === 0n) return `${sign}${hundredths / 100n}`
  return `${sign}${hundredths / 100n}.${String(hundredths % 100n).padStart(2, '0')}`
}

export function overWords(
  label: string,
  weightPct: string,
  limitPct: string,
  sector: boolean
): string {
  const what = sector ? `The sector ${label}` : label
  return `${what} is ${two(weightPct)}% of what is held here, above your ${two(limitPct)}% limit.`
}

export const untaggedWords = (weightPct: string): string =>
  `Untagged: ${two(weightPct)}%; tag them to check sectors.`

/** The largest `TOP` shares and any further one over the limit, largest first (a tie by name). */
function bars(sums: ReadonlyMap<string, number>, total: number, limit: bigint): RiskWeight[] {
  const all = [...sums.entries()]
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([label, value]) => ({
      label,
      weight_pct: pct(value, total) ?? formatPct(0n),
      over_limit: BigInt(value) * 100n * SCALE > limit * BigInt(total),
    }))
  return all.filter((bar, at) => at < TOP || bar.over_limit)
}

/** `RiskConcentration` of what is held: the service's own rule, on the values given. */
export function concentration(
  holdings: readonly Holding[],
  maxHoldingPct: string,
  maxSectorPct: string
): RiskConcentration {
  // What is held: one line per instrument, values above nothing only.
  const held = new Map<string, Holding>()
  for (const each of holdings) {
    if (each.value_paise > 0) {
      const have = held.get(each.key)
      held.set(each.key, { ...each, value_paise: each.value_paise + (have?.value_paise ?? 0) })
    }
  }
  const total = [...held.values()].reduce((sum, each) => sum + each.value_paise, 0)
  if (total === 0) return { holdings: [], sectors: [], untagged_pct: null, flags: [] }
  const names = new Map<string, number>()
  const sectors = new Map<string, number>()
  let untagged = 0
  for (const each of held.values()) {
    names.set(each.label, (names.get(each.label) ?? 0) + each.value_paise)
    if (each.sector) sectors.set(each.sector, (sectors.get(each.sector) ?? 0) + each.value_paise)
    else untagged += each.value_paise
  }
  const byName = bars(names, total, scaled(maxHoldingPct))
  const bySector = bars(sectors, total, scaled(maxSectorPct))
  const untaggedPct = pct(untagged, total)
  const flags = [
    ...byName
      .filter((bar) => bar.over_limit)
      .map((bar) => overWords(bar.label, bar.weight_pct, maxHoldingPct, false)),
    ...bySector
      .filter((bar) => bar.over_limit)
      .map((bar) => overWords(bar.label, bar.weight_pct, maxSectorPct, true)),
  ]
  if (untagged > 0 && untaggedPct !== null) flags.push(untaggedWords(untaggedPct))
  return { holdings: byName, sectors: bySector, untagged_pct: untaggedPct, flags }
}
