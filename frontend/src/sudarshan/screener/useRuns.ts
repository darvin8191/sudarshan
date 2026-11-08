import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { type ScreenerError, screenerApi } from './api'
import { runsKey } from './keys'
import type { RunRow, RunSet, RunsPage } from './types'

/** Runs read at a time. */
export const RUNS_PAGE = 30

/**
 * A screen's history, newest first, a page at a time: "Show older" asks for the runs before the
 * oldest one held. A pushed run puts a row at the top of what is held and reads again (push.ts);
 * never on a timer.
 */
export function useRuns(screenId: number) {
  const query = useInfiniteQuery<
    RunsPage,
    ScreenerError,
    RunRow[],
    ReturnType<typeof runsKey>,
    number | null
  >({
    queryKey: runsKey(screenId),
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) =>
      screenerApi.runs(screenId, { before: pageParam, limit: RUNS_PAGE }, signal),
    getNextPageParam: (last) =>
      last.more && last.runs.length > 0 ? last.runs[last.runs.length - 1].id : undefined,
    select: (data) => data.pages.flatMap((page) => page.runs),
    staleTime: 60_000,
    retry: false,
  })
  return query
}

/** One past run's set (rewind): the rows as the run found them. */
export function useRunSet(runId: number | null) {
  return useQuery<RunSet, ScreenerError>({
    queryKey: ['sudarshan', 'screener', 'run', runId],
    queryFn: ({ signal }) => screenerApi.run(runId as number, signal),
    enabled: runId !== null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })
}
