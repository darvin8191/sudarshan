// SUDARSHAN M3.8 (GA7.6): alerts the owner has not looked at, cleared when he opens their screen.
import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { screenerApi } from './api'
import { STATE_KEY, TODAY_KEY } from './keys'
import type { ScreenerState, ScreenRow, Today } from './types'

const sending = new Set<string>()

/**
 * Mark a screen's alerts looked at, up to the run the page shows (one that comes after stays
 * new), and move what the page holds: the screen's own count and the Dashboard card's. A mark
 * that could not be sent changes nothing here: the alerts stay counted, and the next open of the
 * screen, or its next run, sends it again.
 */
export async function markAlertsSeen(
  client: QueryClient,
  screenId: number,
  through: number
): Promise<void> {
  // one mark for a screen and a run at a time, however often the page asks while it is on its way
  const key = `${screenId}:${through}`
  if (sending.has(key)) return
  sending.add(key)
  let left: number
  try {
    left = (await screenerApi.alertsSeen({ screen_id: screenId, through })).unseen_alerts
  } catch {
    return
  } finally {
    sending.delete(key)
  }
  client.setQueryData<ScreenerState>(STATE_KEY, (held) =>
    held
      ? {
          ...held,
          screens: held.screens.map((row) =>
            row.id === screenId && row.last_run_id <= through ? { ...row, unseen_alerts: 0 } : row
          ),
        }
      : held
  )
  client.setQueryData<Today>(TODAY_KEY, (held) => (held ? { ...held, unseen_alerts: left } : held))
  void client.invalidateQueries({ queryKey: TODAY_KEY })
}

/**
 * An open screen is a looked-at screen: while its page is on screen (not while the phone shows
 * another app), the alerts it holds are marked looked at, when it opens and when a run raises
 * one. Sent once for each run, never on a timer.
 */
export function useAlertsSeen(row: Pick<ScreenRow, 'id' | 'last_run_id' | 'unseen_alerts'>): void {
  const client = useQueryClient()
  const { id, last_run_id: through } = row
  const unseen = row.unseen_alerts ?? 0
  useEffect(() => {
    if (unseen <= 0) return
    const mark = () => {
      if (document.visibilityState !== 'visible') return
      document.removeEventListener('visibilitychange', mark)
      void markAlertsSeen(client, id, through)
    }
    mark()
    if (document.visibilityState !== 'visible') {
      document.addEventListener('visibilitychange', mark)
    }
    return () => document.removeEventListener('visibilitychange', mark)
  }, [client, id, through, unseen])
}
