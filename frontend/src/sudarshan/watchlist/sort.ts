/**
 * Sorting an open list (M8.7, F6), free of React like `rows.ts`.
 *
 * A sort is a snapshot: the order is worked out once, from the figures of one
 * instant, and kept as a rank per row. A tick changes a row's figures and never
 * its place (`useSortOrder` takes a new snapshot only when the owner asks).
 */

import { FOLLOW_CAP } from './follow'
import { describe, rowView, type Snapshot } from './rows'
import type { LiveRow } from './useLiveRows'
import type { Basis, Pref, Sort } from './v2/types'

/** What a list is when nothing was chosen: its own order, change from the previous close. */
export const DEFAULT_PREF: Pref = { sort: 'list', descending: false, basis: 'close', held: false }

/** Whether the choice leaves the list as it is: then no line is shown under the tabs. */
export function isDefaultView(pref: Pref): boolean {
  return pref.sort === 'list' && pref.basis === 'close'
}

/** The sorts that rank on prices, and so need a read of them first. */
export function needsPrices(sort: Sort): boolean {
  return sort === 'change' || sort === 'price'
}

export const SORTS: ReadonlyArray<{ sort: Sort; label: string }> = [
  { sort: 'list', label: 'List order' },
  { sort: 'change', label: '% change' },
  { sort: 'price', label: 'Price' },
  { sort: 'name', label: 'Name' },
  { sort: 'exchange', label: 'Exchange' },
]

/** The two ways round for a sort, as the sheet lists them: numbers say high or low, names A to Z. */
export function directions(sort: Sort): ReadonlyArray<{ descending: boolean; label: string }> {
  return sort === 'name' || sort === 'exchange'
    ? [
        { descending: false, label: 'A to Z' },
        { descending: true, label: 'Z to A' },
      ]
    : [
        { descending: true, label: 'High first' },
        { descending: false, label: 'Low first' },
      ]
}

/** "% change, high first": the sort in the words of the line and the sheet. */
export function sortWords(pref: Pref): string {
  const label = SORTS.find((each) => each.sort === pref.sort)?.label ?? ''
  if (pref.sort === 'list') return label
  const way = directions(pref.sort).find((each) => each.descending === pref.descending)?.label ?? ''
  return `${pref.sort === 'change' ? label : label.toLowerCase()}, ${way.replace(/^(High|Low)/, (word) => word.toLowerCase())}`
}

/** The sentence of the change basis, and of a list that is not on the previous close. */
export const BASIS_LINE = 'Change from today’s open'

/**
 * The direction a pick starts in: price and change sort high first, names A to Z.
 * Moving between two sorts of one kind keeps the owner's direction.
 */
export function pickSort(pref: Pref, sort: Sort): Pref {
  const kind = (each: Sort) => (each === 'name' || each === 'exchange' ? 'text' : 'number')
  if (sort === 'list') return { ...pref, sort, descending: false }
  const same = pref.sort !== 'list' && kind(pref.sort) === kind(sort)
  // `descending` is "high first" for numbers and "Z to A" for names.
  return { ...pref, sort, descending: same ? pref.descending : kind(sort) === 'number' }
}

/** What a row is ranked on, taken at the instant of the sort. */
export interface SortEntry {
  id: number
  title: string
  exchange: string
  ltp: number | null
  changePercent: number | null
}

export function entryOf(row: LiveRow): SortEntry {
  return {
    id: row.item.id,
    title: describe(row.item.symbol, row.item.exchange).title,
    exchange: row.item.exchange,
    ltp: row.view.ltp,
    changePercent: row.view.changePercent,
  }
}

/** F9: a row ranked on the one snapshot of the whole list, never on its tick. */
export function entryFromSnapshot(
  row: LiveRow,
  snapshot: Snapshot | undefined,
  basis: Basis
): SortEntry {
  const view = rowView(snapshot, undefined, basis)
  return { ...entryOf(row), ltp: view.ltp, changePercent: view.changePercent }
}

const WORDS = new Intl.Collator('en', { sensitivity: 'base', numeric: true })

/**
 * The entries in sort order. A tie keeps the order the entries came in (the list's
 * order), a row with no figure to rank on goes last in both directions, and
 * `list` is the order given. `descending` is high first for prices and changes and Z to A for names.
 */
export function sortEntries(entries: readonly SortEntry[], pref: Pref): SortEntry[] {
  if (pref.sort === 'list') return [...entries]
  const sign = pref.descending ? -1 : 1
  const number = (entry: SortEntry) => (pref.sort === 'price' ? entry.ltp : entry.changePercent)
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      if (pref.sort === 'name' || pref.sort === 'exchange') {
        const text = (e: SortEntry) => (pref.sort === 'name' ? e.title : e.exchange)
        return sign * WORDS.compare(text(a.entry), text(b.entry)) || a.index - b.index
      }
      const x = number(a.entry)
      const y = number(b.entry)
      if (x === null || y === null) return x === y ? a.index - b.index : x === null ? 1 : -1
      return sign * (x < y ? -1 : x > y ? 1 : 0) || a.index - b.index
    })
    .map(({ entry }) => entry)
}

/** Each row's place, across the groups: the rows of a group sort among themselves, sections stay. */
export function rankGroups(
  groups: ReadonlyArray<readonly SortEntry[]>,
  pref: Pref
): Map<number, number> {
  const rank = new Map<number, number>()
  for (const group of groups)
    for (const entry of sortEntries(group, pref)) rank.set(entry.id, rank.size)
  return rank
}

/** Ids by a rank map: a row the rank does not know (added since) goes after those it does, in list order. */
export function byRank<T>(
  items: readonly T[],
  idOf: (item: T) => number,
  rank: ReadonlyMap<number, number>
): T[] {
  return items
    .map((item, index) => ({ item, index, at: rank.get(idOf(item)) ?? Number.POSITIVE_INFINITY }))
    .sort((a, b) => (a.at === b.at ? a.index - b.index : a.at < b.at ? -1 : 1))
    .map(({ item }) => item)
}

/** The longest list the page follows whole. A longer one reads every row before it sorts (F9). */
export const LONG_LIST = FOLLOW_CAP

/**
 * A list longer than the cap follows only a window of its rows, so a price sort on it first reads
 * one snapshot of every row (one call per 500 keys) and ranks on that instant (`entryFromSnapshot`).
 * `useSortOrder` asks before it ranks.
 */
export function needsFullSnapshot(count: number): boolean {
  return count > LONG_LIST
}
