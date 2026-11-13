// SUDARSHAN M14.3, JD4: the heat map, weekday against the hour a trade was entered (India's
// clock). Every cell carries its net with its sign and its count as text; the tint (stronger for
// a larger result) is a second sign and never the only one. Nothing is drawn for an hour with no
// trades beyond a dash.
import { cn } from '@/lib/utils'
import { directionOf, formatRupees } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { compactPaise } from './analyticsWords'
import { type HeatCell, PAISE_PER_RUPEE } from './types'

const WEEKDAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const
const SECTION = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'

/** Hours the grid shows: the market's (9 to 15) widened to any hour that has trades. */
export function hoursOf(cells: readonly HeatCell[]): number[] {
  const hours = cells.map((c) => c.hour)
  const lo = Math.min(9, ...hours)
  const hi = Math.max(15, ...hours)
  return Array.from({ length: hi - lo + 1 }, (_, n) => lo + n)
}

/** Weekdays the grid shows: Monday to Friday, and Saturday or Sunday when they have trades. */
export function weekdaysOf(cells: readonly HeatCell[]): number[] {
  const last = Math.max(4, ...cells.map((c) => c.weekday))
  return Array.from({ length: last + 1 }, (_, n) => n)
}

/** A tint by sign, in three strengths by how large the result is against the largest. */
export function tintOf(net: number, biggest: number): string {
  if (net === 0 || biggest === 0) return ''
  const share = Math.abs(net) / biggest
  const colour = net > 0 ? '--up' : '--down'
  const strength = share > 0.66 ? 34 : share > 0.33 ? 22 : 12
  return `color-mix(in oklab, var(${colour}) ${strength}%, white)`
}

export function HeatMap({ cells }: { cells: readonly HeatCell[] }) {
  if (cells.length === 0) {
    return (
      <section aria-label="Heat map" className={SECTION}>
        <h2 className="text-base font-bold">When you trade best</h2>
        <p className="mt-2 text-sm text-[color:var(--text-secondary)]">
          Results by weekday and entry hour are shown here once there are closed trades.
        </p>
      </section>
    )
  }
  const hours = hoursOf(cells)
  const days = weekdaysOf(cells)
  const at = new Map(cells.map((c) => [`${c.weekday}:${c.hour}`, c]))
  const biggest = Math.max(...cells.map((c) => Math.abs(c.net_paise)))
  return (
    <section aria-label="Heat map" className={`${SECTION} flex flex-col gap-2`}>
      <h2 className="text-base font-bold">When you trade best</h2>
      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">
        Net by weekday and the hour the trade was entered; the small number is the trades.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[19rem] table-fixed border-separate border-spacing-1 text-center">
          <caption className="sr-only">Net and trades by weekday and entry hour</caption>
          <thead>
            <tr>
              <td className="w-9" />
              {hours.map((h) => (
                <th
                  key={h}
                  scope="col"
                  className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d}>
                <th
                  scope="row"
                  className="text-left text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]"
                >
                  {WEEKDAY[d]}
                </th>
                {hours.map((h) => {
                  const cell = at.get(`${d}:${h}`)
                  if (!cell) {
                    return (
                      <td
                        key={h}
                        data-testid="heat-cell"
                        aria-label={`${WEEKDAY[d]} ${h}:00: no trades`}
                        className="h-12 rounded-md border border-[color:var(--divider)] text-[0.75rem] text-[color:var(--text-tertiary)]"
                      >
                        -
                      </td>
                    )
                  }
                  const dir = directionOf(cell.net_paise / PAISE_PER_RUPEE)
                  return (
                    <td
                      key={h}
                      data-testid="heat-cell"
                      aria-label={`${WEEKDAY[d]} ${h}:00: net ${formatRupees(cell.net_paise / PAISE_PER_RUPEE, { signed: true })}, ${cell.trips} ${cell.trips === 1 ? 'trade' : 'trades'}`}
                      style={{ background: tintOf(cell.net_paise, biggest) || undefined }}
                      className={cn(
                        'h-12 rounded-md border border-[color:var(--divider)] px-0.5 align-middle',
                        TREND_TEXT[dir]
                      )}
                    >
                      <span className="inline-flex items-center gap-0.5 text-[0.75rem] leading-none font-bold">
                        <TrendArrow direction={dir} className="size-[0.4375rem]" />
                        {compactPaise(cell.net_paise)}
                      </span>
                      <span className="block text-[0.625rem] leading-tight font-medium text-[color:var(--text-secondary)]">
                        {cell.trips}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
