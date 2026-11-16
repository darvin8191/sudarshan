// SUDARSHAN M13.7 (P8 U2): the TRINETRA header line, section 3.6's words with the age of the last
// reading always in them. "Real-time" only while this screen is in the owner's room and TRINETRA's
// readings are fresh, so pushes are arriving; never "Live" for a reading. Not a live region: the age
// changes by the minute, and announcing that would drown everything else.
import { CirclePause, Clock, RefreshCw, Unplug } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Hearing } from './live'
import { type FreshNow, freshLine } from './words'

const TONE: Record<string, string> = {
  fresh: 'text-[color:var(--text-secondary)]',
  stale: 'text-[color:var(--warning-text)]',
  stopped: 'text-[color:var(--text-secondary)]',
  never: 'text-[color:var(--text-secondary)]',
}

const ICON = { fresh: Clock, stale: Clock, stopped: CirclePause, never: Unplug } as const

/** Updates arrive as TRINETRA publishes them: in the room, and its readings fresh. */
export function realTime(fresh: FreshNow, hearing: Hearing): boolean {
  return hearing === 'joined' && fresh.status === 'fresh'
}

export function FreshnessLine({
  fresh,
  hearing = 'unknown',
  connected = false,
  compact = false,
  className,
}: {
  fresh: FreshNow
  /** Given on /trinetra only: whether this screen hears the pushes. */
  hearing?: Hearing
  connected?: boolean
  compact?: boolean
  className?: string
}) {
  const Icon = ICON[fresh.status as keyof typeof ICON] ?? Clock
  const reconnecting = connected && hearing === 'not_joined' && fresh.status !== 'never'
  return (
    <div
      data-testid="trinetra-freshness"
      data-fresh={fresh.status}
      className={cn('flex flex-col gap-1', className)}
    >
      <p
        className={cn(
          'flex flex-wrap items-start gap-x-2 gap-y-1 leading-snug font-medium',
          compact ? 'text-[0.8125rem]' : 'text-[0.9375rem]',
          TONE[fresh.status] ?? TONE.fresh
        )}
      >
        <span className="flex min-w-0 items-start gap-1.5">
          <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>{freshLine(fresh)}</span>
        </span>
        {realTime(fresh, hearing) && (
          <span
            data-updates="real-time"
            className="inline-flex items-center gap-1.5 text-xs font-semibold whitespace-nowrap text-[color:var(--live-ink)]"
          >
            <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-current" />
            Real-time
          </span>
        )}
      </p>
      {reconnecting && (
        <p className="flex items-start gap-1.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          <RefreshCw aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          Reconnecting: a new reading shows here once the connection is back.
        </p>
      )}
    </div>
  )
}
