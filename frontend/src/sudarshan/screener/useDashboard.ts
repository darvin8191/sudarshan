import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import type { ScreenerError } from './api'
import { screenerApi } from './api'
import { cardKey, readCard } from './dashboardCache'
import { DASHBOARD_KEY } from './keys'
import { noteSeen } from './push'
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
      noteSeen(read.last_run_id)
      return read
    },
    staleTime: 60_000,
    retry: false,
  })
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set())
  const [tries, setTries] = useState(0)

  const pending = (query.data?.widgets ?? [])
    .filter((card) => card.rows_pending)
    .map(cardKey)
    .join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: `tries` restarts the reads after a retry
  useEffect(() => {
    if (!pending) return
    let alive = true
    void (async () => {
      for (const key of pending.split(',')) {
        if (!alive) return
        const held = client
          .getQueryData<Dashboard>(DASHBOARD_KEY)
          ?.widgets.find((card) => cardKey(card) === key)
        if (!held?.rows_pending) continue
        try {
          await readCard(client, held)
        } catch {
          if (alive) setFailed((now) => new Set(now).add(key))
        }
      }
    })()
    return () => {
      alive = false
    }
  }, [client, pending, tries])

  const retry = useCallback(() => {
    setFailed(new Set())
    setTries((n) => n + 1)
  }, [])
  return { query, failed, retry }
}
