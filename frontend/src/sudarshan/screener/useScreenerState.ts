import { useQuery } from '@tanstack/react-query'
import type { ScreenerError } from './api'
import { screenerApi } from './api'
import { STATE_KEY } from './keys'
import { noteSeen } from './push'
import type { ScreenerState } from './types'

/**
 * The page's state: read when it opens, then kept current by what the server pushes (push.ts),
 * and read again after a reconnect. Never on a timer. A failed read is shown at once, in the
 * server's words, rather than tried again.
 */
export function useScreenerState() {
  return useQuery<ScreenerState, ScreenerError>({
    queryKey: STATE_KEY,
    queryFn: async () => {
      const state = await screenerApi.state()
      noteSeen(state.last_run_id)
      return state
    },
    staleTime: 30_000,
    retry: false,
  })
}
