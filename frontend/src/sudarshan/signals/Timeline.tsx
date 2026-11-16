// SUDARSHAN M13.7 (P8 U4): TRINETRA's timeline: every change of state with its time, the reading
// before and after, newest first, 50 a page, and "Load older". Each row opens "Why". It begins with
// the first document TRINETRA publishes (nothing from before that can be read). A row older than 30
// minutes keeps its arrow and word and loses its colour, as a reading does.
import { ArrowRight, History, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { problemOf } from './api'
import { BiasMark } from './BiasMark'
import { CHANGE_WORDS, type Change, COLOUR_FOR_MS } from './types'
import { useTimeline } from './useSignals'
import { confidenceWords, moduleName, timeWords } from './words'

const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

function Row({
  change,
  now,
  onWhy,
}: {
  change: Change
  now: number
  onWhy: (change: Change) => void
}) {
  const figure = confidenceWords(change.confidence)
  const colour = now - change.at_ms < COLOUR_FOR_MS
  return (
    <li data-change={change.id} data-kind={change.kind}>
      <button
        type="button"
        onClick={() => onWhy(change)}
        className="flex min-h-12 w-full flex-col items-start gap-0.5 rounded-lg py-2 text-left outline-none hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="text-[0.75rem] font-semibold text-[color:var(--text-tertiary)]">
          {timeWords(change.at_ms, now)} · {moduleName(change.module)} · {change.instrument} ·{' '}
          {CHANGE_WORDS[change.kind] ?? CHANGE_WORDS.state}
        </span>
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.9375rem]">
          {change.from_label && (
            <>
              <span className="break-words text-[color:var(--text-secondary)]">
                {change.from_label}
              </span>
              <ArrowRight aria-hidden="true" className="size-3.5 shrink-0" />
              <span className="sr-only">to</span>
            </>
          )}
          <span className="font-semibold break-words">{change.to_label}</span>
          <BiasMark bias={change.bias} colour={colour} />
          {figure !== null && (
            <span className="text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
              {figure}
            </span>
          )}
        </span>
        <span className="text-[0.8125rem] font-semibold text-[color:var(--brand)]">Why</span>
      </button>
    </li>
  )
}

export function Timeline({ now, onWhy }: { now: number; onWhy: (change: Change) => void }) {
  const timeline = useTimeline(true)
  const changes = timeline.data?.pages.flatMap((page) => page.changes) ?? []
  return (
    <section
      aria-labelledby="sd-trinetra-timeline"
      className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 pt-3 pb-2 shadow-[var(--shadow-card)]"
    >
      <h2 id="sd-trinetra-timeline" className="flex items-center gap-2 text-[0.9375rem] font-bold">
        <History aria-hidden="true" className="size-4" />
        Timeline
      </h2>
      {timeline.isPending ? (
        <output className="flex min-h-12 items-center gap-2 text-[0.875rem] text-[color:var(--text-secondary)]">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Reading the timeline
        </output>
      ) : timeline.isError && changes.length === 0 ? (
        <div className="flex flex-col items-start gap-2 py-1">
          <p role="alert" className="flex items-start gap-1.5 text-[0.875rem] font-medium">
            <TriangleAlert
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
            />
            {problemOf(timeline.error).message}
          </p>
          <button type="button" onClick={() => void timeline.refetch()} className={ACTION}>
            <RefreshCw aria-hidden="true" className="size-4" />
            Try again
          </button>
        </div>
      ) : changes.length === 0 ? (
        <p className="py-2 text-[0.875rem] text-[color:var(--text-secondary)]">
          No changes yet. The timeline begins with the first reading TRINETRA publishes.
        </p>
      ) : (
        <>
          <ol
            aria-label="Changes, newest first"
            className="flex flex-col divide-y divide-[color:var(--divider)]"
          >
            {changes.map((change) => (
              <Row key={change.id} change={change} now={now} onWhy={onWhy} />
            ))}
          </ol>
          {timeline.isFetchNextPageError && (
            <p
              role="alert"
              className="text-[0.8125rem] font-medium text-[color:var(--warning-text)]"
            >
              {problemOf(timeline.error).message}
            </p>
          )}
          {timeline.hasNextPage && (
            <button
              type="button"
              onClick={() => void timeline.fetchNextPage()}
              disabled={timeline.isFetchingNextPage}
              className={`${ACTION} my-1`}
            >
              {timeline.isFetchingNextPage ? (
                <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
              ) : null}
              {timeline.isFetchingNextPage ? 'Loading older changes' : 'Load older'}
            </button>
          )}
        </>
      )}
    </section>
  )
}
