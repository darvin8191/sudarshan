// SUDARSHAN M13.5 (P8 U3): TRINETRA's gap bias for today's open, on the dashboard from 08:45 until
// the open at 09:15, and after it until the first change of a gap-bias reading after the open (a
// lapse at the open included), never past 15:30 (India time). Only readings resting on today's data
// are shown: yesterday's call is never shown as today's. It renders nothing while the view is off
// or unanswered, and outside that window.
import { Sunrise, TimerOff } from 'lucide-react'
import { ReadingRow, SignalsCard } from './cardParts'
import { type Reading, type State, WORDS } from './types'
import { lapsedAt, orderReadings, useSignals } from './useSignals'
import { fill, istAt, istDayOf, moduleName, nextIstMidnight, timeWords } from './words'

/** The window's edges on the India day of `now`. */
function edges(now: number) {
  return { from: istAt(now, 8, 45), open: istAt(now, 9, 15), until: istAt(now, 15, 30) }
}

/** A gap-bias reading changed after the open: it lapsed then, a new state began, or it lost its data. */
function changedAfterOpen(reading: Reading, open: number, now: number): boolean {
  if (lapsedAt(reading, now) && reading.valid_until_ms !== null && reading.valid_until_ms >= open) {
    return true
  }
  if (reading.since_ms !== null && reading.since_ms > open) return true
  return reading.quality === 'no_data' && reading.as_of_ms >= open
}

/** The gap-bias readings the card shows at `now` (server clock), or null when it shows nothing. */
export function gapRows(state: State, now: number): Reading[] | null {
  const { from, open, until } = edges(now)
  if (now < from || now >= until) return null
  const today = istDayOf(now)
  const rows = state.readings.filter(
    (each) => each.module === 'gap_bias' && istDayOf(each.as_of_ms) === today
  )
  if (rows.length === 0) return null
  if (now >= open && rows.some((each) => changedAfterOpen(each, open, now))) return null
  return orderReadings(rows, now)
}

/** The moments the card may appear or go by the clock alone. */
function gapMoments(_state: State, now: number): number[] {
  const { from, open, until } = edges(now)
  return [from, open, until, nextIstMidnight(now)]
}

export function GapBiasCard() {
  const { state, fresh, now, problem } = useSignals(gapMoments)
  if (!state || !fresh || problem || fresh.status === 'never') return null
  const rows = gapRows(state, now)
  if (!rows) return null
  return (
    <SignalsCard
      id="sd-trinetra-gap"
      title={`${moduleName('gap_bias')} · gap bias at the open`}
      icon={<Sunrise aria-hidden="true" className="size-4" />}
      fresh={fresh}
    >
      {rows.map((reading) => {
        const lapsed = lapsedAt(reading, now)
        return (
          <ReadingRow
            key={reading.instrument}
            reading={reading}
            colour={fresh.colour && !lapsed}
            extra={
              lapsed && reading.valid_until_ms !== null ? (
                <span className="flex items-center gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
                  <TimerOff aria-hidden="true" className="size-3.5" />
                  {fill(WORDS.lapsed, { time: timeWords(reading.valid_until_ms, now) })}
                </span>
              ) : reading.valid_until_ms !== null ? (
                <span className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
                  Holds until {timeWords(reading.valid_until_ms, now)}
                </span>
              ) : null
            }
          />
        )
      })}
    </SignalsCard>
  )
}
