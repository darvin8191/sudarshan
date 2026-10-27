import { Clock, Moon, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ago, type FeedLine } from './live'

const TEXT = 'inline-flex items-center gap-1.5 text-xs font-semibold whitespace-nowrap'

/**
 * The words for a line that is not real-time, with the age of the figures
 * where it has one. Shared with the chart's own marker, which draws them
 * beside its own glyph.
 */
export function feedWords(
  line: Exclude<FeedLine, { kind: 'live' } | { kind: 'idle' }>,
  now: number
): string {
  switch (line.kind) {
    case 'closed':
      return 'Market closed'
    case 'waiting':
      return 'Waiting for prices'
    case 'delayed':
      return `${line.stale ? 'Stale · updated' : 'Updated'} ${ago(now - line.at)}`
    case 'polling':
      return `${line.stale ? 'Stale' : 'Delayed'} · updated ${ago(now - line.at)}`
    case 'connecting':
      if (!line.again) return 'Connecting'
      return line.at === null ? 'Reconnecting' : `Reconnecting · updated ${ago(now - line.at)}`
  }
}

/**
 * Where a set of figures stands: "Real-time" while their ticks are arriving (not
 * "Live", which the app bar already uses for the live trading mode), and
 * otherwise what is happening instead: (re)connecting, how old the figures are
 * ("Stale" when ticks are due and have stopped), or that the market is closed.
 * Always a word, with a mark beside it, never a coloured dot alone. Not a live
 * region: the age changes every second, and announcing that would drown
 * everything else. Nothing at all where nothing is held (`idle`): no price is
 * coming there, so there is nothing to wait for.
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
  if (line.kind === 'idle') return null
  if (line.kind === 'live') {
    return (
      <span data-feed="live" className={cn(TEXT, 'text-[color:var(--live-ink)]', className)}>
        <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-current" />
        Real-time
      </span>
    )
  }

  const stale =
    (line.kind === 'delayed' || line.kind === 'polling' || line.kind === 'connecting') && line.stale
  const Icon = line.kind === 'closed' ? Moon : line.kind === 'connecting' ? RefreshCw : Clock

  return (
    <span
      data-feed={
        (line.kind === 'delayed' || line.kind === 'polling') && stale ? 'stale' : line.kind
      }
      className={cn(
        TEXT,
        stale ? 'text-[color:var(--warning-text)]' : 'text-muted-foreground',
        className
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {feedWords(line, now)}
    </span>
  )
}
