import {
  Bell,
  ChevronDown,
  CircleAlert,
  Clock,
  Hourglass,
  Info,
  LoaderCircle,
  Minus,
  Plus,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react'
import { memo } from 'react'
import { cn } from '@/lib/utils'
import { EmptyCard, LoadingCards, OUTLINED, STALE } from '@/sudarshan/books/cards/parts'
import { istTime } from '@/sudarshan/books/time'
import type { Hearing } from './push'
import type { RunRow } from './types'
import { WORDS } from './types'
import { useRuns } from './useRuns'

const DAY = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})

/** "9 Nov 10:30:05": a run's moment as the clock in India shows it. */
export function runMoment(at: number): string {
  return `${DAY.format(at)} ${istTime(at)}`
}

/** What the service says of a run beyond its counts: still waiting to leave, not checked, not finished, alerts. */
export const STATUS_WORDS: Record<string, string> = {
  superseded: 'Replaced by a newer check',
  failed: 'This check did not finish',
}

export function RunFacts({ run }: { run: RunRow }) {
  const waiting = run.missing ?? 0
  const skipped = run.not_checked ?? 0
  const status = run.status && run.status !== 'done' ? run.status : null
  if (!status && !waiting && !skipped && !run.alerts) return null
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
      {status && (
        <span
          data-run-status={status}
          className="inline-flex items-center gap-1 font-semibold text-[color:var(--warning-text)]"
        >
          <CircleAlert aria-hidden="true" className="size-3.5" />
          {STATUS_WORDS[status] ?? 'This check did not finish'}
        </span>
      )}
      {waiting > 0 && (
        <span className="inline-flex items-center gap-1">
          <Hourglass aria-hidden="true" className="size-3.5" />
          {waiting} waiting to leave
        </span>
      )}
      {skipped > 0 && (
        <span className="inline-flex items-center gap-1">
          <Info aria-hidden="true" className="size-3.5" />
          {skipped} not checked
        </span>
      )}
      {run.alerts && (
        <span className="inline-flex items-center gap-1">
          <Bell aria-hidden="true" className="size-3.5" />
          {run.alerts}
        </span>
      )}
    </span>
  )
}

const RunLine = memo(function RunLine({
  run,
  onOpen,
}: {
  run: RunRow
  onOpen: (id: number) => void
}) {
  return (
    <li data-run={run.id}>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onOpen(run.id)}
        className="flex min-h-14 w-full flex-col justify-center gap-0.5 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="flex items-center justify-between gap-3">
          <span className="text-[0.9375rem] font-semibold">{runMoment(run.at)}</span>
          <span className="text-[0.9375rem] font-bold">
            {run.matches} {run.matches === 1 ? 'match' : 'matches'}
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          <span>{run.candle} candle</span>
          <span className="inline-flex items-center gap-0.5 font-semibold text-[color:var(--up)]">
            <Plus aria-hidden="true" className="size-3.5" />
            {run.entered} entered
          </span>
          <span className="inline-flex items-center gap-0.5 font-semibold text-[color:var(--down)]">
            <Minus aria-hidden="true" className="size-3.5" />
            {run.exited} exited
          </span>
        </span>
        <RunFacts run={run} />
        {run.marks.length > 0 && (
          <span className="flex flex-col gap-0.5">
            {run.marks.map((mark) => (
              <span
                key={mark}
                className="inline-flex items-start gap-1 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
              >
                <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
                {mark}
              </span>
            ))}
          </span>
        )}
      </button>
    </li>
  )
})

/**
 * A screen's runs, newest first (design 4.4): when, the candle it judged, how many matched, who
 * came in and who left, and any mark the service put on it (a skipped check, a late feed). A tap
 * opens that run's set. Older runs come a page at a time. A run pushed while this is open joins
 * the top at once; while this screen is not in the owner's room the list says it may be behind.
 */
export function History({
  screenId,
  hearing,
  onOpen,
}: {
  screenId: number
  hearing: Hearing
  onOpen: (runId: number) => void
}) {
  const query = useRuns(screenId)
  const runs = query.data

  if (!runs) {
    if (query.isPending) return <LoadingCards />
    return (
      <section
        role="alert"
        className="flex flex-col items-start gap-3 rounded-[1.125rem] border border-destructive bg-card px-4 py-3.5"
      >
        <p className="flex items-start gap-2 text-sm font-semibold">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          {query.error?.message ?? WORDS.unreadable}
        </p>
        <button type="button" onClick={() => void query.refetch()} className={OUTLINED}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </section>
    )
  }

  if (runs.length === 0) {
    return <EmptyCard title="No runs yet" hint="Each check of this screen is listed here." />
  }

  return (
    <div className="flex flex-col gap-2">
      {hearing === 'not_joined' && (
        <p
          data-history="not-current"
          className="inline-flex items-start gap-1.5 px-1 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
        >
          <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          New runs may be missing until this screen is receiving updates again.
        </p>
      )}
      <section
        aria-label="Runs"
        className={cn(
          'overflow-hidden rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]',
          query.isError && STALE
        )}
      >
        <ul className="divide-y">
          {runs.map((run) => (
            <RunLine key={run.id} run={run} onOpen={onOpen} />
          ))}
        </ul>
      </section>
      {query.isFetchNextPageError && (
        <p role="alert" className="px-1 text-sm font-semibold text-destructive">
          {WORDS.unreadable}
        </p>
      )}
      {query.hasNextPage && (
        <button
          type="button"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
          className={OUTLINED}
        >
          {query.isFetchingNextPage ? (
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          ) : (
            <ChevronDown aria-hidden="true" className="size-4" />
          )}
          Show older runs
        </button>
      )}
    </div>
  )
}
