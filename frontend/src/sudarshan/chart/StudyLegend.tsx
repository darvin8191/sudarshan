/**
 * The indicators' legend on the phone chart (M4.2, C3): one quiet line per
 * study at the top left of its pane, with its values at the last bar, or at
 * the candle a held finger reads. Text only, on a plate over the candles, so
 * it reads in sunlight; nothing in it is a control (the sheet holds those).
 *
 * It re-reads the values once a frame at most, when the price store says a
 * tick came, as the price header does.
 */
import { useSyncExternalStore } from 'react'
import type { LegendRow, PhoneChart } from './phoneChart'
import type { PriceStore } from './prices'

/** How far a line sits below the one above it in the same pane, in CSS pixels. */
const LINE = 18

export function StudyLegend({
  owner,
  prices,
  heldTime,
  below,
}: {
  owner: PhoneChart
  prices: PriceStore
  /** The candle being read, or null for the last. */
  heldTime: number | null
  /** Space kept clear at the top of the price pane (the candle reading's card). */
  below: number
}) {
  // A tick moves the forming bar, and with it the values at the last bar.
  useSyncExternalStore(prices.subscribe, prices.get)
  const rows = owner.legendRows(heldTime)
  if (!rows.length) return null
  const seen = new Map<number, number>()
  const placed = rows.map((row) => {
    const n = seen.get(row.pane) ?? 0
    seen.set(row.pane, n + 1)
    return { row, top: row.top + (row.pane === 0 ? below : 0) + 4 + n * LINE }
  })
  return (
    <div className="pointer-events-none absolute inset-0 z-[3]" data-testid="study-legend">
      {placed.map(({ row, top }) => (
        <Line key={row.key} row={row} top={top} />
      ))}
    </div>
  )
}

function Line({ row, top }: { row: LegendRow; top: number }) {
  return (
    <p
      className="absolute left-2 max-w-[calc(100%-5rem)] truncate rounded bg-card/85 px-1 text-[0.6875rem] leading-4 font-semibold text-[color:var(--text-primary)]"
      style={{ top }}
    >
      {row.title}
      {row.params && (
        <span className="font-medium text-[color:var(--text-tertiary)]"> {row.params}</span>
      )}
      {row.note ? (
        <span className="font-medium text-[color:var(--text-secondary)]"> · {row.note}</span>
      ) : (
        row.values.map((value, i) => (
          <span key={i} className="ml-1.5 tabular-nums">
            {value}
          </span>
        ))
      )}
    </p>
  )
}
