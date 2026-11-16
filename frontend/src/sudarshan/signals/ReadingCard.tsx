// SUDARSHAN M13.7 (P8 U2): one card per (module, index) on /trinetra: the label, the bias as arrow
// and word, the confidence as a figure and a bar, "Since 11 Nov, 3 sessions", the levels and
// TRINETRA's own word on its inputs; "Why" opens the explanation. A stale reading takes the stale
// style (grey edge, clock icon); a lapsed one says "Lapsed 09:15" and loses its colour.
import { CircleSlash, Clock, MessageSquareText, TimerOff, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { BiasMark } from './BiasMark'
import { HORIZON_WORDS, type Reading, WORDS } from './types'
import { lapsedAt } from './useSignals'
import {
  confidenceWords,
  type FreshNow,
  fill,
  levelRows,
  moduleName,
  sinceWords,
  timeWords,
} from './words'

const CARD = 'min-w-0 rounded-[1.125rem] border bg-card px-4 pt-3 pb-1 shadow-[var(--shadow-card)]'

/** A swing module's figure is a probability (the contract puts it in `confidence`). */
export function figureName(module: string): string {
  return module.startsWith('swing') ? 'Probability' : 'Confidence'
}

/** The confidence as a bar beside its figure; the figure is what is read. */
export function ConfidenceBar({ value, muted }: { value: number; muted: boolean }) {
  const share = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <span
      aria-hidden="true"
      className="block h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--divider)]"
    >
      <span
        className={cn(
          'block h-full rounded-full',
          muted ? 'bg-[color:var(--text-tertiary)]' : 'bg-[color:var(--brand)]'
        )}
        style={{ width: `${share}%` }}
      />
    </span>
  )
}

/** TRINETRA's word on its inputs, when it is not "ok". */
export function QualityNote({ quality }: { quality: Reading['quality'] }) {
  if (quality !== 'degraded' && quality !== 'no_data') return null
  const Icon = quality === 'no_data' ? CircleSlash : TriangleAlert
  return (
    <p className="flex items-start gap-1.5 text-[0.8125rem] leading-snug font-medium text-[color:var(--warning-text)]">
      <Icon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
      {quality === 'no_data' ? WORDS.no_data : WORDS.degraded}
    </p>
  )
}

export function ReadingCard({
  reading,
  fresh,
  now,
  onWhy,
}: {
  reading: Reading
  fresh: FreshNow
  now: number
  onWhy: (reading: Reading) => void
}) {
  const lapsed = lapsedAt(reading, now)
  const colour = fresh.colour && !lapsed
  const stale = fresh.status !== 'fresh'
  const figure = confidenceWords(reading.confidence)
  const since = sinceWords(reading.since_ms, reading.sessions, now)
  const levels = levelRows(reading.levels)
  const name = moduleName(reading.module)
  return (
    <article
      aria-label={`${name}, ${reading.instrument}`}
      data-module={reading.module}
      data-instrument={reading.instrument}
      data-lapsed={lapsed ? 'true' : undefined}
      data-fresh={fresh.status}
      className={cn(CARD, stale && 'border-[color:var(--border-strong)]')}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="flex min-w-0 items-center gap-1.5 text-[0.9375rem] font-bold">
          {stale && (
            <Clock
              aria-hidden="true"
              className="size-4 shrink-0 text-[color:var(--text-secondary)]"
            />
          )}
          <span className="truncate">
            {name} · {reading.instrument}
          </span>
        </h3>
        <span className="shrink-0 text-[0.75rem] font-semibold text-[color:var(--text-tertiary)]">
          {HORIZON_WORDS[reading.horizon] ?? ''}
        </span>
      </div>
      <p className="mt-1 text-[1.0625rem] leading-snug font-semibold break-words">
        {reading.label}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.9375rem]">
        <BiasMark bias={reading.bias} colour={colour} />
        {figure !== null && (
          <span className="font-semibold">
            {figureName(reading.module)} {figure}
          </span>
        )}
      </div>
      {reading.confidence !== null && figure !== null && (
        <div className="mt-1.5">
          <ConfidenceBar value={reading.confidence} muted={!colour} />
        </div>
      )}
      {since && (
        <p className="mt-1.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {since}
        </p>
      )}
      {levels.length > 0 && (
        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[0.8125rem]">
          {levels.map(([label, value]) => (
            <div key={label} className="flex gap-1">
              <dt className="text-[color:var(--text-secondary)]">{label}</dt>
              <dd className="font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="mt-1.5 flex flex-col gap-1">
        <QualityNote quality={reading.quality} />
        {lapsed && reading.valid_until_ms !== null && (
          <p className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            <TimerOff aria-hidden="true" className="size-3.5 shrink-0" />
            {fill(WORDS.lapsed, { time: timeWords(reading.valid_until_ms, now) })}
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onWhy(reading)}
        aria-label={`Why: ${name}, ${reading.instrument}`}
        className="-ml-2 inline-flex min-h-12 items-center gap-1.5 rounded-lg px-2 text-[0.9375rem] font-semibold text-[color:var(--brand)] outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
      >
        <MessageSquareText aria-hidden="true" className="size-4" />
        Why
      </button>
    </article>
  )
}
