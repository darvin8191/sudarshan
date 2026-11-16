// SUDARSHAN M13.6 (P8 U3): TRINETRA's swing-reversal readings of both lanes (SANDHI end-of-day and
// intraday) for NIFTY and BANKNIFTY, on the dashboard: each with its label, bias and probability.
// It renders nothing while the view is off or unanswered, or with no current swing reading. Levels
// on the chart are the charts design's, from the same data.
import { Repeat } from 'lucide-react'
import { ReadingRow, SignalsCard } from './cardParts'
import type { State } from './types'
import { lapsedAt, orderReadings, useSignals } from './useSignals'
import { moduleName } from './words'

const SWING = new Set(['swing_eod', 'swing_intraday'])

/** The swing readings the card lists at `now`: current ones only, end-of-day first. */
export function swingRows(state: State, now: number) {
  return orderReadings(
    state.readings.filter((each) => SWING.has(each.module) && !lapsedAt(each, now)),
    now
  )
}

export function SwingCard() {
  const { state, fresh, now, problem } = useSignals()
  if (!state || !fresh || problem || fresh.status === 'never') return null
  const rows = swingRows(state, now)
  if (rows.length === 0) return null
  return (
    <SignalsCard
      id="sd-trinetra-swing"
      title="SANDHI · swing reversal"
      icon={<Repeat aria-hidden="true" className="size-4" />}
      fresh={fresh}
    >
      {rows.map((reading) => (
        <ReadingRow
          key={`${reading.module}:${reading.instrument}`}
          reading={reading}
          name={moduleName(reading.module).replace(/^SANDHI /, '')}
          colour={fresh.colour}
        />
      ))}
    </SignalsCard>
  )
}
