import type { QueryClient, QueryKey } from '@tanstack/react-query'
import type { WatchlistItem } from '@/api/watchlist'
import { MAX_NAME_LENGTH, WATCHLISTS_KEY } from '../useWatchlists'
import { problemOf } from './api'
import { groupsOf } from './sections'
import type { ArrangeReq, View } from './types'
import { viewKey } from './useListView'

/** M8.4 F5: the words of arranging, copying and moving, the same as the server's where it says them. */
export const BUILT_IN = 'A built-in list cannot be changed. Make a copy to arrange it.'
export { LISTS_FULL } from '../useWatchlists' // review L14: one sentence, the menu's
export const NAME_LONG = `List names can be up to ${MAX_NAME_LENGTH} letters.`
export const NOTHING = 'This section has no symbols to copy.'

const symbols = (n: number) => `${n} ${n === 1 ? 'symbol' : 'symbols'}`

/** The target cannot take what was picked: W8's sentence (`words/arrange.py: room`), before sending. */
export function roomWords(name: string, left: number, picked: number, fromSection: boolean) {
  const more = left <= 0 ? '0 more symbols' : `${left} more ${left === 1 ? 'symbol' : 'symbols'}`
  const what = fromSection ? 'this section has' : 'you picked'
  return `${name} has room for ${more}; ${what} ${picked}. Make room in ${name} or pick another list.`
}

export function copiedWords(move: boolean, added: number, skipped: number, name: string) {
  const first = `${move ? 'Moved' : 'Copied'} ${symbols(added)} to ${name}.`
  return skipped > 0 ? `${first} ${skipped} already there.` : first
}

/** One symbol of the list being arranged, in the draft. */
export interface DraftRow {
  id: number
  exchange: string
  symbol: string
  section: number | null
}

/** The arrangement being edited: the section order and every symbol's section. */
export interface Draft {
  sections: number[]
  rows: DraftRow[]
}

/** The draft of what `view` shows now (the OpenAlgo items it lists, then any it does not list yet). */
export function draftOf(view: View, items: readonly WatchlistItem[]): Draft {
  const byId = new Map(items.map((item) => [item.id, item]))
  const rows: DraftRow[] = []
  const sections: number[] = []
  for (const group of groupsOf(view)) {
    if (group.section) sections.push(group.section.id)
    for (const id of group.ids) {
      const item = byId.get(id)
      if (item) {
        byId.delete(id)
        rows.push({
          id,
          exchange: item.exchange,
          symbol: item.symbol,
          section: group.section?.id ?? null,
        })
      }
    }
  }
  for (const item of byId.values())
    rows.push({ id: item.id, exchange: item.exchange, symbol: item.symbol, section: null })
  return { sections, rows }
}

/** The draft with what the list no longer holds dropped (a symbol removed meanwhile). */
export function fitTo(draft: Draft, items: readonly WatchlistItem[]): Draft {
  const ids = new Set(items.map((item) => item.id))
  return draft.rows.every((row) => ids.has(row.id))
    ? draft
    : { ...draft, rows: draft.rows.filter((row) => ids.has(row.id)) }
}

export const inGroup = (draft: Draft, section: number | null) =>
  draft.rows.filter((row) => row.section === section)

/** A symbol up or down a place inside its own section. */
export function moveRow(draft: Draft, id: number, by: -1 | 1): Draft {
  const row = draft.rows.find((each) => each.id === id)
  if (!row) return draft
  const peers = inGroup(draft, row.section)
  const other = peers[peers.findIndex((each) => each.id === id) + by]
  if (!other) return draft
  const rows = [...draft.rows]
  const [i, j] = [rows.indexOf(row), rows.indexOf(other)]
  rows[i] = other
  rows[j] = row
  return { ...draft, rows }
}

/** A symbol into another section (or No section), at its end. */
export function sendRow(draft: Draft, id: number, section: number | null): Draft {
  const row = draft.rows.find((each) => each.id === id)
  if (!row || row.section === section) return draft
  return { ...draft, rows: [...draft.rows.filter((each) => each.id !== id), { ...row, section }] }
}

/** A whole section up or down a place. */
export function moveSection(draft: Draft, id: number, by: -1 | 1): Draft {
  const at = draft.sections.indexOf(id)
  if (at < 0 || !draft.sections[at + by]) return draft
  const sections = [...draft.sections]
  ;[sections[at], sections[at + by]] = [sections[at + by], sections[at]]
  return { ...draft, sections }
}

/** The one `arrange`: the section order, each symbol's section, the item order (No section first). */
export function arrangeReq(draft: Draft, id: number, version: string): ArrangeReq {
  const ordered = [null, ...draft.sections].flatMap((section) => inGroup(draft, section))
  return {
    id,
    version,
    sections: draft.sections,
    items: ordered.map((row) => ({
      exchange: row.exchange,
      symbol: row.symbol,
      section_id: row.section,
    })),
  }
}

export const sameDraft = (a: Draft, b: Draft) => JSON.stringify(a) === JSON.stringify(b)

/**
 * One write, then the lists it touched are read again whatever the answer (after a conflict they
 * are the other screen's). Each key is read exactly: `WATCHLISTS_KEY` is a prefix of every v2 key,
 * and a write re-reads only what it changed. Resolves to what it answered, or the server's sentence and its code;
 * never a status code.
 */
export async function writeThenRead<T>(
  client: QueryClient,
  keys: QueryKey[],
  send: () => Promise<T>
): Promise<{ done: T | null; said: string | null; code: string | null }> {
  let out: { done: T | null; said: string | null; code: string | null }
  try {
    out = { done: await send(), said: null, code: null }
  } catch (error) {
    const problem = problemOf(error)
    out = { done: null, said: problem.message, code: problem.code }
  }
  await Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey, exact: true })))
  return out
}

export const arrangeKeys = (ref: string): QueryKey[] => [viewKey(ref), WATCHLISTS_KEY]
