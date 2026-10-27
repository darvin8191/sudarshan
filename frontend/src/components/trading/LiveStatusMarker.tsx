/**
 * The marker a chart pane shows when its prices are not arriving in real time:
 * Delayed while a quote every few seconds stands in for the stream,
 * Reconnecting while the stream is down, Stale when nothing has come for a
 * while in market hours, Market closed outside them. Each with the age of the
 * figures where it has one.
 *
 * The words are the app's own (`feedWords`), drawn beside an engine glyph at
 * the terminal's line weight; never a colour alone, and nothing at all while
 * prices are real-time. Not a live region: the age changes every second and
 * announcing that would drown everything else. The sentence behind it is its
 * title and is read with it.
 */
import { useEffect, useState } from 'react'
import { ChromeIcon } from '@/lib/trading/glyphs'
import { type LiveStatus, liveDetail, needsMarker } from '@/lib/trading/liveStatus'
import { cn } from '@/lib/utils'
import { feedWords } from '@/sudarshan/dashboard/LiveLine'
import type { FeedLine } from '@/sudarshan/dashboard/live'

type Quiet = Exclude<FeedLine, { kind: 'live' } | { kind: 'idle' }>

function isStale(line: Quiet): boolean {
  return 'stale' in line && line.stale
}

function glyphOf(line: Quiet): string {
  if (line.kind === 'closed') return 'market-closed'
  if (line.kind === 'connecting') return 'refresh'
  return isStale(line) ? 'warning' : 'clock'
}

function feedOf(line: Quiet): string {
  return isStale(line) && line.kind !== 'connecting' ? 'stale' : line.kind
}

/** The time now, a second at a time while there is an age to show. */
function useSecond(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  return now
}

export function LiveStatusMarker({
  status,
  className,
}: {
  status: LiveStatus | null
  className?: string
}) {
  const shown = status !== null && needsMarker(status.line)
  const now = useSecond(shown)
  if (!status || !shown || status.line.kind === 'live' || status.line.kind === 'idle') return null
  const line = status.line
  const detail = liveDetail(status, now)
  return (
    <span
      data-feed={feedOf(line)}
      title={detail}
      className={cn(
        'pointer-events-auto inline-flex w-fit items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-semibold leading-4 whitespace-nowrap',
        line.kind === 'closed'
          ? 'border-border bg-card text-[color:var(--stale)]'
          : 'border-[color:var(--warning-fill)] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]',
        className
      )}
    >
      <ChromeIcon id={glyphOf(line)} size={12} />
      <span>{feedWords(line, now)}</span>
      <span className="sr-only">. {detail}</span>
    </span>
  )
}
