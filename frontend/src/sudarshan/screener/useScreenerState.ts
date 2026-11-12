import { useQuery } from '@tanstack/react-query'
import type { ScreenerError } from './api'
import { STATE_KEY } from './keys'
import { readState, STATE_STALE_MS } from './push'
import type { ScreenerState } from './types'

/**
 * The page's state: read when it opens, then kept current by what the server pushes (push.ts),
 * and read again after a reconnect. Never on a timer. A failed read is shown at once, in the
 * server's words, rather than tried again.
 */
export function useScreenerState() {
  return useQuery<ScreenerState, ScreenerError>({
    queryKey: STATE_KEY,
    queryFn: readState,
    staleTime: STATE_STALE_MS,
    retry: false,
  })
}
