// SUDARSHAN M7.15: the Upstox holdings by class and by sector, as horizontal bars (never a pie).
// Read-only: no bar places or changes anything. The bars are worked out on the phone from the
// holdings as ticks move them (allocation.ts), with the class and sector the service gives for
// each holding; a holding with none is in a bar that says so, never guessed.
import { Check, Info } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { formatAmount } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { CHOICE, OFF, ON } from '../sheets/TabFields'
import { rupeesText } from '../tab/format'
import type { AllocationRow, UpstoxAllocation, UpstoxHolding, UpstoxPart } from '../types'
import { BOOK_BY, type BookBy, bookBars, linesOf, unclassified } from './allocation'
import { holdingNow, holdingsTotals } from './live'
import { CARD, Figure, NoFigures, PartNote, ReadStamp } from './parts'
import { instrumentOf, type UpstoxLive } from './useUpstoxLive'

const BY_LABELS: Record<BookBy, string> = { class: 'Class', sector: 'Sector' }

/** "3 of your holdings have no class or sector yet": said by the phone for the holdings shown. */
export function unclassifiedWords(count: number): string {
  return `${count} of your holdings ${count === 1 ? 'has' : 'have'} no class or sector yet. SUDARSHAN does not guess them: a share takes the class and sector you gave it in one of your own tabs.`
}

const pctOf = (row: AllocationRow): number => {
  const n = Number(row.pct)
  return Number.isFinite(n) ? n : 0
}

function Bars({ by, rows, stale }: { by: BookBy; rows: readonly AllocationRow[]; stale: boolean }) {
  return (
    <ul aria-label={`Allocation by ${BY_LABELS[by].toLowerCase()}`} className="flex flex-col gap-2">
      {rows.map((row) => (
        <li
          key={row.key}
          data-bar={row.key}
          className="relative flex min-h-12 w-full items-center overflow-hidden rounded-[0.875rem] border border-[color:var(--border-strong)]"
        >
          <span
            aria-hidden="true"
            className="absolute inset-y-0 left-0 bg-[color:var(--brand-tint)]"
            style={{ width: `${Math.min(100, Math.max(0, pctOf(row)))}%` }}
          />
          <span className="relative flex w-full items-center justify-between gap-3 px-3 py-2">
            <span className="min-w-0 truncate text-sm font-semibold">{row.label}</span>
            <span
              className={cn(
                'flex shrink-0 flex-col items-end text-sm',
                stale && 'text-muted-foreground'
              )}
            >
              <span className="font-bold">{formatAmount(pctOf(row), 2)}%</span>
              <span className="text-xs text-[color:var(--text-secondary)]">
                {rupeesText(row.value_paise)} · {row.holdings}{' '}
                {row.holdings === 1 ? 'holding' : 'holdings'}
              </span>
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export function AllocationView({
  part,
  allocation,
  reading = false,
  live,
}: {
  part: UpstoxPart<UpstoxHolding[]> | undefined
  /** The service's classes; undefined while they are read or when they could not be. */
  allocation: UpstoxAllocation | undefined
  /** Whether the classes are still being read (else, without them, the read failed). */
  reading?: boolean
  live: UpstoxLive
}) {
  const [by, setBy] = useState<BookBy>('class')
  const rows = part?.data
  if (!rows) return <NoFigures part={part}>The holdings could not be read yet.</NoFigures>
  if (rows.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <PartNote part={part} />
        <NoFigures part={part}>No holdings in your Upstox account.</NoFigures>
        <ReadStamp at={part.read_at} />
      </div>
    )
  }
  const readAt = part.read_at
  const now = rows.map((row) => {
    const key = instrumentOf(row)
    return holdingNow(row, key ? live.ticks.get(key) : undefined, readAt)
  })
  const totals = holdingsTotals(now)
  const allStale = live.line.kind === 'delayed' && live.line.stale
  const lines = linesOf(
    rows,
    now.map((n) => n.valuePaise),
    allocation?.classes ?? []
  )
  // Classes are known only when the service could read the holdings it classified.
  const known = allocation !== undefined && allocation.allocation !== null
  const loose = known ? unclassified(lines) : 0
  return (
    <div className="flex flex-col gap-3">
      <PartNote part={part} />
      <section aria-label="Allocation" className={cn(CARD, 'flex flex-col gap-3 px-4 py-3.5')}>
        <dl>
          <Figure
            label="Current value"
            note={`${rows.length} ${rows.length === 1 ? 'holding' : 'holdings'}`}
          >
            <span className={cn(allStale && 'text-muted-foreground')}>
              {rupeesText(totals.valuePaise)}
            </span>
          </Figure>
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <LiveLine line={live.line} now={live.now} />
          <ReadStamp at={readAt} />
        </div>
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">Allocation by</legend>
          {BOOK_BY.map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={by === key}
              onClick={() => setBy(key)}
              className={cn(CHOICE, 'min-h-12', by === key ? ON : OFF)}
            >
              {by === key && <Check aria-hidden="true" className="size-4" />}
              {BY_LABELS[key]}
            </button>
          ))}
        </fieldset>
        {known ? (
          <Bars by={by} rows={bookBars(by, lines)} stale={allStale} />
        ) : reading ? (
          <p className="text-sm text-[color:var(--text-secondary)]" aria-live="polite">
            Reading the class and sector of each holding
          </p>
        ) : (
          <p className="text-sm font-medium text-[color:var(--text-secondary)]" aria-live="polite">
            {allocation?.note ??
              'The class and sector of your holdings could not be read, so no bar is shown.'}
          </p>
        )}
      </section>
      {loose > 0 && (
        <p
          data-unclassified={loose}
          className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
        >
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {unclassifiedWords(loose)}
        </p>
      )}
    </div>
  )
}
