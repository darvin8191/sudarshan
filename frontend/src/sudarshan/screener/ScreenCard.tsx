import { ArrowUpRight, Clock, ListChecks } from 'lucide-react'
import { memo } from 'react'
import { cn } from '@/lib/utils'
import { CARD } from '@/sudarshan/books/cards/parts'
import { StatusChip } from './marks'
import type { ScreenRow } from './types'

/** '7 match · 3 new today': the figures a card leads with. */
export function countsWords(row: Pick<ScreenRow, 'matches' | 'entered_today'>): string {
  const match = `${row.matches} match`
  return row.entered_today > 0 ? `${match} · ${row.entered_today} new today` : match
}

/**
 * One screen as a card (design 5.1): its name, where it looks and how often, its status with the
 * reason when it is Stopped, how many match and how many are new today, and when it checks next.
 * The whole card is one 48 dp target that opens the screen.
 */
export const ScreenCard = memo(function ScreenCard({
  row,
  onOpen,
}: {
  row: ScreenRow
  onOpen: (id: number) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(row.id)}
      data-screen={row.id}
      aria-label={`${row.name}, ${row.where}, ${row.status}, ${countsWords(row)}`}
      className={cn(CARD, 'min-h-[5.25rem] gap-1.5')}
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0 truncate text-base font-bold">{row.name}</span>
        <StatusChip status={row.status} />
      </span>
      <span className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
        {row.where}
      </span>
      {row.status === 'stopped' && row.status_reason && (
        <span className="text-[0.8125rem] font-semibold text-[color:var(--warning-text)]">
          {row.status_reason}
        </span>
      )}
      <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.8125rem] font-semibold">
        <span className="inline-flex items-center gap-1">
          <ListChecks aria-hidden="true" className="size-4" />
          {countsWords(row)}
        </span>
        {row.next_check && row.status === 'active' && (
          <span className="inline-flex items-center gap-1 text-[color:var(--text-secondary)]">
            <Clock aria-hidden="true" className="size-4" />
            {row.next_check}
          </span>
        )}
        <ArrowUpRight
          aria-hidden="true"
          className="ml-auto size-4 text-[color:var(--text-tertiary)]"
        />
      </span>
    </button>
  )
})
