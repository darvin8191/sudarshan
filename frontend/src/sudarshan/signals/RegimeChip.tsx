// SUDARSHAN M13.4 (P8 U3): TRINETRA's regime of the charted index in one line, for the phone
// chart's header: "NIFTY · FII distribution ▼ bearish 0.71 · 3 sessions · 2 min old". It opens the
// TRINETRA page. It renders nothing while the view is off or unanswered, for an instrument that is
// not NIFTY or BANKNIFTY or a contract on them, and when there is no current regime reading. A
// stale reading takes the stale style (grey edge, clock icon) and past 30 minutes loses its colour.
import { Clock } from 'lucide-react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { BiasMark } from './BiasMark'
import { underlyingOf } from './stance'
import { type Instrument, PAGE } from './types'
import { lapsedAt, useSignals } from './useSignals'
import { biasWord, confidenceWords, freshLine, shortAge } from './words'

/** The chart's instrument, as the regime it is read against: NIFTY or BANKNIFTY, else none. */
export function regimeInstrument(symbol: string, exchange: string): Instrument | null {
  return underlyingOf(symbol, exchange)
}

export function RegimeChip({
  instrument,
  className,
}: {
  /** NIFTY or BANKNIFTY; null renders nothing, and reads nothing (the chart's instrument is neither). */
  instrument: Instrument | null
  className?: string
}) {
  return instrument === null ? null : <Chip instrument={instrument} className={className} />
}

function Chip({ instrument, className }: { instrument: Instrument; className?: string }) {
  const { state, fresh, now, problem } = useSignals()
  if (!state || !fresh || problem || fresh.status === 'never') return null
  const reading = state.readings.find(
    (each) => each.module === 'regime' && each.instrument === instrument
  )
  if (!reading || lapsedAt(reading, now)) return null
  const timely = fresh.status === 'fresh'
  const figure = confidenceWords(reading.confidence)
  const sessions =
    reading.sessions !== null && reading.sessions > 0
      ? `${reading.sessions} ${reading.sessions === 1 ? 'session' : 'sessions'}`
      : null
  const age =
    fresh.age !== null && state.freshness.published_ms !== null
      ? `${shortAge(now - state.freshness.published_ms)} old`
      : null
  const spoken = [
    `TRINETRA regime, ${instrument}: ${reading.label}, ${biasWord(reading.bias)}`,
    figure !== null ? `confidence ${figure}` : null,
    sessions,
    freshLine(fresh),
  ]
    .filter(Boolean)
    .join('. ')
  return (
    <Link
      to={PAGE}
      aria-label={spoken}
      data-testid="regime-chip"
      data-fresh={fresh.status}
      className={cn(
        '@container flex min-h-12 shrink-0 items-center border-b bg-card px-3 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
        className
      )}
    >
      <span
        className={cn(
          'flex min-h-8 min-w-0 flex-1 items-center gap-1.5 rounded-full border px-2.5 text-[0.8125rem] font-semibold',
          timely ? 'border-[color:var(--divider)]' : 'border-[color:var(--border-strong)]'
        )}
      >
        {!timely && (
          <Clock
            aria-hidden="true"
            className="size-3.5 shrink-0 text-[color:var(--text-secondary)]"
          />
        )}
        <span className="shrink-0 font-bold">{instrument}</span>
        <span aria-hidden="true" className="shrink-0 text-[color:var(--text-tertiary)]">
          ·
        </span>
        <span className="min-w-0 truncate">{reading.label}</span>
        <BiasMark bias={reading.bias} colour={fresh.colour} />
        {figure !== null && <span className="shrink-0">{figure}</span>}
        {sessions && (
          <span className="hidden shrink-0 text-[color:var(--text-secondary)] @sm:inline">
            · {sessions}
          </span>
        )}
        {age && (
          <span
            className={cn(
              'ml-auto shrink-0 pl-1 font-medium',
              timely ? 'text-[color:var(--text-tertiary)]' : 'text-[color:var(--warning-text)]'
            )}
          >
            {age}
          </span>
        )}
      </span>
    </Link>
  )
}
