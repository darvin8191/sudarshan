import { Clock, Moon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ago, type FeedLine } from './live'

const TEXT = 'inline-flex items-center gap-1.5 text-xs font-semibold whitespace-nowrap'

/**
 * Where a set of figures stands: "Live" while their ticks are arriving, and
 * otherwise how old they are ("Stale" when ticks are due and have stopped), or
 * that the market is closed. Always a word, with a mark beside it, never a
 * coloured dot alone. Not a live region: the age changes every second, and
 * announcing that would drown everything else.
 */
export function LiveLine({
  line,
  now,
  className,
}: {
  line: FeedLine
  now: number
  className?: string
}) {
  if (line.kind === 'live') {
    return (
      <span data-feed="live" className={cn(TEXT, 'text-[color:var(--live-ink)]', className)}>
        <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-current" />
        Live
      </span>
    )
  }

  const stale = line.kind === 'delayed' && line.stale
  const Icon = line.kind === 'closed' ? Moon : Clock
  const text =
    line.kind === 'closed'
      ? 'Market closed'
      : line.kind === 'delayed'
        ? `${stale ? 'Stale · updated' : 'Updated'} ${ago(now - line.at)}`
        : 'Waiting for prices'

  return (
    <span
      data-feed={stale ? 'stale' : line.kind}
      className={cn(
        TEXT,
        stale ? 'text-[color:var(--warning-text)]' : 'text-muted-foreground',
        className
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {text}
    </span>
  )
}
