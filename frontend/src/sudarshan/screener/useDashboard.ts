import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import type { ScreenerError } from './api'
import { screenerApi } from './api'
import {
  cardKey,
  clearUnread,
  isUnread,
  mergeRead,
  readCard,
  rowsBehind,
  useUnread,
} from './dashboardCache'
import { DASHBOARD_KEY } from './keys'
import type { Dashboard } from './types'

/**
 * The dashboard (design 3.7): read once when the view opens, then changed only by what the
 * screener pushes and by the owner's own changes; never on a timer. A card the read had no time
 * to work out (`rows_pending`) is read with the `widget` op, one at a time in card order, and
 * fills in place. One that cannot be read is named in `failed` and read again by `retry`.
 */
export function useDashboard() {
  const client = useQueryClient()
  const query = useQuery<Dashboard, ScreenerError>({
    queryKey: DASHBOARD_KEY,
    queryFn: async ({ signal }) => {
      const read = await screenerApi.dashboard(signal)
      // A fresh layout starts every card's reads again, and never puts an older card over a newer.
      clearUnread()
      return mergeRead(client.getQueryData<Dashboard>(DASHBOARD_KEY), read)
    },
    staleTime: 60_000,
    retry: false,
  })
  const failed = useUnread()
  const [tries, setTries] = useState(0)

  // Cards still to read: those the read had no time for, and those whose rows are behind a pushed run.
  const pending = (query.data?.widgets ?? [])
    .filter((card) => card.rows_pending || rowsBehind(card))
    .map(cardKey)
    .join(',')
  const unreadKeys = [...failed].sort().join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: `tries` restarts the reads after a retry; `unreadKeys` lets the next card go once one has settled
  useEffect(() => {
    if (!pending) return
    let alive = true
    void (async () => {
      for (const key of pending.split(',')) {
        if (!alive) return
        const held = client
          .getQueryData<Dashboard>(DASHBOARD_KEY)
          ?.widgets.find((card) => cardKey(card) === key)
        if (!held || !(held.rows_pending || rowsBehind(held))) continue
        // A card that failed waits for Try again: later cards filling in never re-read it.
        if (isUnread(key)) continue
        try {
          await readCard(client, held)
        } catch {
          // readCard has marked it unread.
        }
      }
    })()
    return () => {
      alive = false
    }
  }, [client, pending, tries, unreadKeys])

  const retry = useCallback(() => {
    clearUnread()
    setTries((n) => n + 1)
  }, [])
  return { query, failed, retry }
}
