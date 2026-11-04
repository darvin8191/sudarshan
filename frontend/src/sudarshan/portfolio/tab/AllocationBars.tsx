// SUDARSHAN M7.33: allocation as horizontal bars (design 4.8), never a pie. Each bar carries its
// label and its percentage as text on the bar, and the bars are sorted largest first. A tap
// filters the holdings to that slice; a tap on the same bar takes the filter off.
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatAmount } from '@/sudarshan/dashboard/format'
import { CHOICE, OFF, ON } from '../sheets/TabFields'
import { ALLOCATION_BY, type AllocationBy, type AllocationRow } from '../types'
import { rupeesText } from './format'

export const BY_LABELS: Record<AllocationBy, string> = {
  class: 'Class',
  sector: 'Sector',
  mcap: 'Market cap',
  account: 'Account',
  member: 'Member',
  tag: 'Tag',
}

const pctOf = (row: AllocationRow): number => {
  const n = Number(row.pct)
  return Number.isFinite(n) ? n : 0
}

export function AllocationBars({
  by,
  onBy,
  rows,
  isActive,
  onPick,
}: {
  by: AllocationBy
  onBy: (next: AllocationBy) => void
  rows: readonly AllocationRow[]
  /** Whether the holdings are filtered to this slice now. */
  isActive: (row: AllocationRow) => boolean
  onPick: (row: AllocationRow) => void
}) {
  const sorted = [...rows].sort((a, b) => b.value_paise - a.value_paise || pctOf(b) - pctOf(a))
  return (
    <section
      aria-label="Allocation"
      className="flex flex-col gap-3 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 className="text-[0.9375rem] font-bold">Allocation</h2>
      <fieldset className="flex flex-wrap gap-2">
        <legend className="sr-only">Allocation by</legend>
        {ALLOCATION_BY.map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={by === key}
            onClick={() => onBy(key)}
            className={cn(CHOICE, 'min-h-12', by === key ? ON : OFF)}
          >
            {BY_LABELS[key]}
          </button>
        ))}
      </fieldset>
      {sorted.length === 0 ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          Nothing to split by {BY_LABELS[by].toLowerCase()} yet.
        </p>
      ) : (
        <ul
          aria-label={`Allocation by ${BY_LABELS[by].toLowerCase()}`}
          className="flex flex-col gap-2"
        >
          {sorted.map((row) => {
            const active = isActive(row)
            const width = Math.min(100, Math.max(0, pctOf(row)))
            return (
              <li key={row.key}>
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => onPick(row)}
                  className={cn(
                    'relative flex min-h-12 w-full items-center overflow-hidden rounded-[0.875rem] border text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                    active
                      ? 'border-2 border-[color:var(--brand)]'
                      : 'border-[color:var(--border-strong)]'
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="absolute inset-y-0 left-0 bg-[color:var(--brand-tint)]"
                    style={{ width: `${width}%` }}
                  />
                  <span className="relative flex w-full items-center justify-between gap-3 px-3 py-2">
                    <span className="flex min-w-0 items-center gap-1.5 text-sm font-semibold">
                      {active && <Check aria-hidden="true" className="size-4 shrink-0" />}
                      <span className="truncate">{row.label}</span>
                    </span>
                    <span className="flex shrink-0 flex-col items-end text-sm">
                      <span className="font-bold">{formatAmount(pctOf(row), 2)}%</span>
                      <span className="text-xs text-[color:var(--text-secondary)]">
                        {rupeesText(row.value_paise)} · {row.holdings}{' '}
                        {row.holdings === 1 ? 'holding' : 'holdings'}
                      </span>
                    </span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
