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
