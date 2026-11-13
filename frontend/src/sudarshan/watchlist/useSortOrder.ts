import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Snapshot } from './rows'
import {
  byRank,
  entryFromSnapshot,
  entryOf,
  needsFullSnapshot,
  needsPrices,
  rankGroups,
} from './sort'
import type { LiveRow } from './useLiveRows'
import type { Group } from './v2/sections'
import type { Pref } from './v2/types'

export interface SortedRows {
  /** The rows in the sort's order (the list's own order when there is no sort). */
  rows: LiveRow[]
  /** The sections' groups with their ids in the sort's order, when the list has sections. */
  groups: Group[] | undefined
  /** When the snapshot the order was ranked on was taken, or null. */
  at: number | null
  /** F9: when the prices a long list was ranked on were read, or null (a short list ranks on what it shows). */
  readAt: number | null
  /** Take a new snapshot from the figures as they are now: the only thing that moves the rows besides a pick. */
  again: () => void
}

/**
 * A sort is a snapshot (F6). The order is ranked from the figures of one instant and kept: a tick
 * changes a row's figures and never its place. A new snapshot is taken when the list opens (and
 * when its first prices have been read, if the sort ranks on prices and none were there yet), when
 * the owner picks another sort or change basis, and when he taps Sort again. A row added since stays
 * at the end of its group until then; a row removed leaves.
 *
 * `ready` says the first read of the list's prices has landed (or failed). `active` is false while
 * there is nothing to sort (the switch off, the list in its own order, editing).
 */
export function useSortOrder({
  rows,
  groups,
  pref,
  listKey,
  ready,
  active,
  fullRead,
}: {
  rows: LiveRow[]
  groups: Group[] | undefined
  pref: Pref
  /** Changes when another list opens. */
  listKey: string
  ready: boolean
  active: boolean
  /** F9: one read of every row's quote, at once; null when it failed. Needed for a list longer than the window. */
  fullRead?: () => Promise<ReadonlyMap<string, Snapshot> | null>
}): SortedRows {
  const [taken, setTaken] = useState<{
    rank: Map<number, number>
    at: number
    readAt: number | null
  } | null>(null)
  const [again, setAgain] = useState(0)
  const latest = useRef({ rows, groups })
  useLayoutEffect(() => {
    latest.current = { rows, groups }
  })
  const key = `${listKey}|${pref.sort}|${pref.descending}|${pref.basis}|${again}`
  const takenFor = useRef<string | null>(null)
  const populated = rows.length > 0
  const sort = pref.sort
  const descending = pref.descending

  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` stands for the list, the sort and the tap; rows and groups are read from `latest` at the instant of the snapshot, never as a dependency (that would re-rank on a tick)
  useEffect(() => {
    if (!active) {
      takenFor.current = null
      return
    }
    if (takenFor.current === key || !populated) return
    // F9: a price sort on a list longer than the window reads one snapshot of every row, then ranks
    // every row on that instant. A failed read takes no snapshot; Sort again tries it once more.
    if (needsPrices(sort) && fullRead && needsFullSnapshot(latest.current.rows.length)) {
      takenFor.current = key
      void fullRead().then((quotes) => {
        if (takenFor.current !== key) return
        if (quotes === null) return // `takenFor` stays: only another pick or Sort again reads once more
        const { rows: now, groups: layout } = latest.current
        const byId = new Map(
          now.map((row) => [row.item.id, entryFromSnapshot(row, quotes.get(row.key), pref.basis)])
        )
        const sets = (layout ?? [{ section: null, ids: now.map((row) => row.item.id) }]).map(
          (group) => group.ids.flatMap((id) => byId.get(id) ?? [])
        )
        const readAt = Math.max(0, ...[...quotes.values()].map((held) => held.at))
        setTaken({
          rank: rankGroups(sets, { ...pref, sort, descending }),
          at: Date.now(),
          readAt: readAt > 0 ? readAt : null,
        })
      })
      return
    }
    if (needsPrices(sort) && !ready) return
    takenFor.current = key
    const { rows: now, groups: layout } = latest.current
    const byId = new Map(now.map((row) => [row.item.id, entryOf(row)]))
    const sets = (layout ?? [{ section: null, ids: now.map((row) => row.item.id) }]).map((group) =>
      group.ids.flatMap((id) => byId.get(id) ?? [])
    )
    setTaken({
      rank: rankGroups(sets, { ...pref, sort, descending }),
      at: Date.now(),
      readAt: null,
    })
  }, [key, populated, ready, active, sort, descending, fullRead])

  const again_ = useCallback(() => setAgain((n) => n + 1), [])
  return useMemo(() => {
    if (!active || taken === null) return { rows, groups, at: null, readAt: null, again: again_ }
    return {
      rows: byRank(rows, (row) => row.item.id, taken.rank),
      groups: groups?.map((group) => ({
        ...group,
        ids: byRank(group.ids, (id) => id, taken.rank),
      })),
      at: taken.at,
      readAt: taken.readAt,
      again: again_,
    }
  }, [active, taken, rows, groups, again_])
}
