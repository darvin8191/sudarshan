import { type QueryClient, useQuery } from '@tanstack/react-query'
import { callOp, type WatchlistsError } from './api'
import type { FlagName, Row, SymbolInfo, View } from './types'
import { LIMITS } from './types'
import { LISTS_KEY, STATE_KEY, useV2On, V2_KEY } from './useListView'

/** M8.6 F8: flags and notes. What the owner reads, the reads, and the writes' version rule. */

export const DEFAULT_FLAG_NAMES = ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'] as const
export const FLAG_IDS = [1, 2, 3, 4, 5] as const

export const PRIVATE_WORDS = 'Notes and flags are yours: the family never sees them.'
export const FLAG_LIST_WORDS = 'Flagged across your lists. Flag or unflag a symbol from its sheet.'
/** The server's own sentence for a note over the limit, said before anything is sent. */
export const NOTE_LONG = 'A note can be up to 500 characters.'

/** The name of flag `n`: the owner's, else the default. */
export function flagName(names: readonly FlagName[] | undefined, flag: number): string {
  return names?.find((name) => name.mark === flag)?.name ?? DEFAULT_FLAG_NAMES[flag - 1] ?? ''
}

/** What a row says about its symbol, by EXCHANGE:SYMBOL. */
export interface RowMark {
  flag: number | null
  name: string
  note: boolean
  expired: boolean
}

/** The rows of a view that carry a flag, a note or an expired contract. */
export function marksOf(
  rows: readonly Row[] | undefined,
  names: readonly FlagName[] | undefined
): ReadonlyMap<string, RowMark> {
  const out = new Map<string, RowMark>()
  for (const row of rows ?? []) {
    if (row.flag === null && !row.has_note && !row.expired) continue
    out.set(row.contract.key.toUpperCase(), {
      flag: row.flag,
      name: row.flag === null ? '' : flagName(names, row.flag),
      note: row.has_note,
      expired: row.expired,
    })
  }
  return out
}

/** What a screen reader hears for a row: "Flag: Breakout. Has a note." */
export function markWords(mark: RowMark | undefined): string {
  if (!mark) return ''
  const parts: string[] = []
  if (mark.flag !== null) parts.push(`Flag: ${mark.name}.`)
  if (mark.note) parts.push('Has a note.')
  if (mark.expired) parts.push('Expired.')
  return parts.join(' ')
}

/** "Swing, Momentum, NIFTY 50 (built-in)": the lists that hold a symbol. */
export function inLists(info: SymbolInfo): string {
  if (info.lists.length === 0) return 'None of your lists'
  return info.lists
    .map((held) => (held.ref.startsWith('list:') ? held.name : `${held.name} (built-in)`))
    .join(', ')
}

/** The flag lists and symbol reads move together with every flag or note. */
export const symbolKey = (key: string) => [...V2_KEY, 'symbol', key] as const

/** One symbol's flag, note, expired reading and version (the `symbol` op), read when its sheet opens. */
export function useSymbolInfo(key: string, enabled = true) {
  const on = useV2On()
  return useQuery<SymbolInfo, WatchlistsError>({
    queryKey: symbolKey(key),
    queryFn: ({ signal }) => callOp('symbol', { k: key }, signal),
    enabled: on && enabled,
    staleTime: 30_000,
    retry: false,
  })
}

/** Read again what a flag or note change touches: the symbol, the flag lists, every open view. */
export async function readAgain(client: QueryClient, key?: string): Promise<void> {
  await Promise.all([
    key ? client.invalidateQueries({ queryKey: symbolKey(key) }) : Promise.resolve(),
    client.invalidateQueries({ queryKey: STATE_KEY }),
    client.invalidateQueries({ queryKey: LISTS_KEY }),
    client.invalidateQueries({ queryKey: [...V2_KEY, 'view'] }),
  ])
}

/** A note as sent: trimmed, and refused in the server's words when it is over the limit. */
export function checkNote(raw: string): string | null {
  return raw.trim().length > LIMITS.note ? NOTE_LONG : null
}

/** The rows of a flag list as the page's own items (read-only; ids are the row's place). */
export function itemsOf(view: View | undefined) {
  return (view?.rows ?? []).map((row, index) => ({
    id: index + 1,
    symbol: row.contract.symbol,
    exchange: row.contract.exchange,
    position: index,
  }))
}
