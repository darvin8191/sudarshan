import { useQuery } from '@tanstack/react-query'
import { type ScreenerError, screenerApi } from './api'
import { resultsKey } from './keys'
import type { Results } from './types'

/**
 * A screen's latest run: read when the screen opens, patched at once by the run pushes
 * (push.ts), read again when a push says the figures of the rows that stayed may have moved, and
 * read again after a reconnect. Never on a timer.
 */
export function useResults(screenId: number) {
  return useQuery<Results, ScreenerError>({
    queryKey: resultsKey(screenId),
    queryFn: ({ signal }) => screenerApi.results(screenId, signal),
    staleTime: 60_000,
    retry: false,
  })
}
