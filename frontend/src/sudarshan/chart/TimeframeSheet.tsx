/**
 * Every timeframe the broker offers (M4.15, C1), in a sheet over the phone
 * chart: tap one to show it, and pin up to six to keep them as chips above
 * the chart. A pin is said by its icon's shape and its word, never by colour.
 */
import { Check, Pin } from 'lucide-react'
import type { IntervalGroup } from '@/lib/trading/intervals'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { intervalLabel, intervalName } from './prefs'

const HEADINGS: Record<string, string> = {
  seconds: 'Seconds',
  minutes: 'Minutes',
  hours: 'Hours',
  days: 'Days',
  weeks: 'Weeks',
  months: 'Months',
}

export function TimeframeSheet({
  open,
  onOpenChange,
  groups,
  current,
  pinned,
  notice,
  onPick,
  onTogglePin,
}: {
  open: boolean
  onOpenChange(open: boolean): void
  /** What the broker offers; null while it is being read. */
  groups: IntervalGroup[] | null
  current: string
  pinned: readonly string[]
  /** Why the last pin was refused, in a sentence. */
  notice: string | null
  onPick(interval: string): void
  onTogglePin(interval: string): void
}) {
  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Timeframe"
      description="Pin up to six to keep them above the chart"
    >
      {notice && (
        <output className="mb-2 block rounded-xl bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-medium text-[color:var(--warning-text)]">
          {notice}
        </output>
      )}
      {groups === null ? (
        <p className="py-4 text-sm text-muted-foreground">Reading the timeframes on offer</p>
      ) : (
        groups.map((group) => (
          <section key={group.label} aria-label={HEADINGS[group.label] ?? group.label}>
            <h3 className="pt-2 pb-1 text-xs font-semibold tracking-wide text-[color:var(--text-tertiary)] uppercase">
              {HEADINGS[group.label] ?? group.label}
            </h3>
            <ul className="grid grid-cols-2 gap-x-2">
              {group.items.map((interval) => {
                const showing = interval === current
                const isPinned = pinned.includes(interval)
                const name = intervalName(interval)
                return (
                  <li key={interval} className="flex min-w-0 items-center">
                    <button
                      type="button"
                      onClick={() => onPick(interval)}
                      aria-pressed={showing}
                      aria-label={`${intervalLabel(interval)}, ${name}`}
                      className={cn(
                        'flex h-12 min-w-0 flex-1 items-center gap-2 rounded-xl px-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        showing
                          ? 'bg-[color:var(--primary-tint)] text-primary shadow-[inset_0_0_0_1.5px_var(--primary)]'
                          : 'hover:bg-accent'
                      )}
                    >
                      <span className="text-[0.9375rem] font-bold">{intervalLabel(interval)}</span>
                      <span className="truncate text-xs font-medium text-muted-foreground">
                        {name}
                      </span>
                      {showing && <Check aria-hidden="true" className="ml-auto size-4 shrink-0" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => onTogglePin(interval)}
                      aria-pressed={isPinned}
                      aria-label={`Pin ${name}`}
                      className="flex size-12 shrink-0 flex-col items-center justify-center rounded-xl text-[color:var(--text-secondary)] hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Pin
                        aria-hidden="true"
                        className={cn('size-4', isPinned && 'fill-current text-primary')}
                      />
                      <span className="text-[0.625rem] leading-tight font-semibold">
                        {isPinned ? 'Pinned' : 'Pin'}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>
        ))
      )}
    </BottomSheet>
  )
}
