import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { keyOf } from '@/sudarshan/dashboard/live'
import { type MemberRow, screenerApi } from './api'
import { membershipKey } from './keys'
import type { Chip } from './types'
import { useScreenerState } from './useScreenerState'
import { useSend } from './useSend'

/**
 * The screener's side of the watchlist page (design 4.3, UI7): which lists a screen feeds, who is
 * in them and what each one's chip says. The service words the chips; this only reads and keys them.
 */

const DAY = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' })

/** The lists screens feed, with the screens that feed each. Empty while the screener is off or unreadable. */
export function useFedLists(): ReadonlyMap<number, string[]> {
  const { data } = useScreenerState()
  return useMemo(() => {
    const fed = new Map<number, string[]>()
    if (!data?.enabled) return fed
    for (const screen of data.screens) {
      if (screen.list_id == null) continue
      fed.set(screen.list_id, [...(fed.get(screen.list_id) ?? []), screen.name])
    }
    return fed
  }, [data])
}

const NO_ROWS: MemberRow[] = []

/** Who a screen put in this list, read once and again whenever a push says a list moved. */
export function useListMembership(listId: number | null, fed: boolean): MemberRow[] {
  const { data } = useQuery({
    queryKey: membershipKey(listId ?? 0),
    queryFn: () => screenerApi.membership({ list: listId as number }),
    enabled: fed && listId !== null,
    staleTime: 60_000,
    retry: false,
    select: (answer) => answer.rows,
  })
  return fed && data ? data : NO_ROWS
}

/** The chip of each instrument still in the list, by EXCHANGE:SYMBOL. */
export function tagsOf(rows: readonly MemberRow[]): ReadonlyMap<string, Chip> {
  const tags = new Map<string, Chip>()
  for (const row of rows) {
    if (row.chip && (row.status === 'active' || row.status === 'missing')) {
      tags.set(keyOf(row.symbol, row.exchange), row.chip)
    }
  }
  return tags
}

/** The symbols the screen let go of today (India's day), for the line under the list. */
export function exitedToday(rows: readonly MemberRow[], now: number): string[] {
  const today = DAY.format(now)
  return rows
    .filter((row) => row.status === 'exited' && row.exited_at !== null)
    .filter((row) => DAY.format(row.exited_at as number) === today)
    .map((row) => row.symbol)
}

export interface PinControl {
  pinned: boolean
  pending: boolean
  /** The service's sentence when a pin was refused, to show in place. */
  problem: string | null
  onToggle: () => void
}

/**
 * Pin and Unpin for one instrument a screen put in the list. Sent once however often it is
 * tapped, with the row's own version so a row that moved meanwhile is refused; the answer replaces
 * the row held.
 */
export function usePin(row: MemberRow | undefined): PinControl | null {
  const client = useQueryClient()
  const send = useSend()
  const toggle = useCallback(async () => {
    if (!row) return
    const ok = await send.run(async () => {
      const next = await screenerApi.member(row.id, row.version, row.pinned ? 'unpin' : 'pin')
      client.setQueryData<{ rows: MemberRow[] }>(membershipKey(row.list_id), (held) =>
        held
          ? { rows: held.rows.map((each) => (each.id === next.id ? { ...each, ...next } : each)) }
          : held
      )
    })
    if (!ok) void client.invalidateQueries({ queryKey: membershipKey(row.list_id) })
  }, [client, row, send])
  if (!row || (row.status !== 'active' && row.status !== 'missing')) return null
  return {
    pinned: row.pinned,
    pending: send.pending,
    problem: send.problem?.message ?? null,
    onToggle: () => void toggle(),
  }
}
