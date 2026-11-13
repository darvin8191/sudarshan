import type { Group, Hit, Segment } from './v2/types'

/** The chips of the search sheet, in the design's order; each sends its `seg`. */
export const CHIPS: ReadonlyArray<{ seg: Segment; label: string }> = [
  { seg: 'all', label: 'All' },
  { seg: 'stocks', label: 'Stocks' },
  { seg: 'futures', label: 'Futures' },
  { seg: 'options', label: 'Options' },
  { seg: 'indices', label: 'Indices' },
  { seg: 'commodities', label: 'Commodities' },
]

export const GROUP_TITLE: Record<Group, string> = {
  stocks: 'Stocks',
  indices: 'Indices',
  futures: 'Futures',
  options: 'Options',
  commodities: 'Commodities',
}

/** The hits in the server's order, one heading for each run of the same group. */
export function runsOf(hits: readonly Hit[]): Array<{ group: Group; hits: Hit[] }> {
  const runs: Array<{ group: Group; hits: Hit[] }> = []
  for (const hit of hits) {
    const last = runs[runs.length - 1]
    if (last && last.group === hit.group) last.hits.push(hit)
    else runs.push({ group: hit.group, hits: [hit] })
  }
  return runs
}

const NSE_INDICES = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50'])
const BSE_INDICES = new Set(['SENSEX', 'BANKEX', 'SENSEX50'])

/**
 * The exchange of the underlying a row can follow (NSE_INDEX, NSE, BSE_INDEX or BSE), or null when it is
 * not one the server follows. The answer's own cash hit says it; else it is read from where the options
 * trade (NFO or BFO) and whether the name is an index.
 */
export function underlyingExchange(underlying: string, hits: readonly Hit[]): string | null {
  const name = underlying.toUpperCase()
  const cash = hits
    .filter(
      (h) =>
        (h.group === 'stocks' || h.group === 'indices') && h.contract.symbol.toUpperCase() === name
    )
    .map((h) => h.contract.exchange)
  const seen = ['NSE_INDEX', 'NSE', 'BSE_INDEX', 'BSE'].find((x) => cash.includes(x))
  if (seen) return seen
  const options = hits.find((h) => h.group === 'options')?.contract.exchange
  if (options === 'NFO') return NSE_INDICES.has(name) ? 'NSE_INDEX' : 'NSE'
  if (options === 'BFO') return BSE_INDICES.has(name) ? 'BSE_INDEX' : 'BSE'
  return null
}
