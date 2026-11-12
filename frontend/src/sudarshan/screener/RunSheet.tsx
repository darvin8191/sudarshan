import { History, Info, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import { ChipMark } from './ChipMark'
import { RunFacts, runMoment } from './History'
import { NotChecked, RunFigures, Values } from './Results'
import { WORDS } from './types'
import { useRunSet } from './useRuns'

/**
 * A past run's set (rewind, design 4.4): the rows as that run found them, in rank order, with the
 * price each had at the run. Nothing here is live and the sheet says so: a rewound price is never
 * shown as a current one.
 */
export function RunSheet({ runId, onClose }: { runId: number | null; onClose: () => void }) {
  const query = useRunSet(runId)
  const set = query.data
  const now = useNow()
  return (
    <BottomSheet
      open={runId !== null}
      onOpenChange={(next) => !next && onClose()}
      title={set ? `Run at ${runMoment(set.run.at)}` : 'Past run'}
    >
      {set ? (
        <div className="flex flex-col gap-3">
          <p
            data-rewind="true"
            className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]"
          >
            <History aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            As it stood at this run, {set.run.candle} candle. Prices are from the run, not live.
          </p>
          <p className="text-base font-bold">
            {set.run.matches} {set.run.matches === 1 ? 'match' : 'matches'}
          </p>
          <RunFacts run={set.run} />
          {set.values_note && (
            <p
              data-values-note="true"
              className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]"
            >
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {set.values_note}
            </p>
          )}
          {set.rows.length > 0 && (
            <ul className="flex flex-col divide-y rounded-xl border">
              {set.rows.map((row) => {
                const named = describe(row.symbol, row.exchange)
                return (
                  <li
                    key={`${row.exchange}:${row.symbol}`}
                    data-result={`${row.exchange}:${row.symbol}`}
                    className="flex min-h-14 items-center gap-3 px-3 py-2"
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-[0.9375rem] font-semibold">
                          {named.title}
                        </span>
                        {row.chip && <ChipMark chip={row.chip} />}
                      </span>
                      <span className="truncate text-xs font-medium text-[color:var(--text-tertiary)]">
                        {named.detail}
                      </span>
                      <Values row={row} />
                    </span>
                    <RunFigures row={row} at={set.run.at} now={now} />
                  </li>
                )
              })}
            </ul>
          )}
          <NotChecked data={set.not_checked} />
        </div>
      ) : query.isError ? (
        <div role="alert" className="flex flex-col items-start gap-3">
          <p className="flex items-start gap-1.5 text-sm font-semibold">
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
        </div>
      ) : (
        <p className="flex items-center gap-2 text-sm font-semibold">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Opening the run
        </p>
      )}
    </BottomSheet>
  )
}
