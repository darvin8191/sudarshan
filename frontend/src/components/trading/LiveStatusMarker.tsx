/**
 * The marker a chart pane shows when its prices are not live: Delayed while
 * prices come from a quote request every few seconds, Stale when nothing has
 * arrived for a while during the session, Market closed outside it.
 *
 * A word and a glyph every time, never a colour alone, and nothing while the
 * prices are live: a live chart needs no badge to say so. The line under the
 * label (its title, and read out to a screen reader) says what it means.
 */
import { ChromeIcon } from '@/lib/trading/glyphs'
import { LIVE_LABEL, type LiveState, type LiveStatus, liveDetail } from '@/lib/trading/liveStatus'
import { cn } from '@/lib/utils'

/** The states that show a marker, with the engine glyph each one carries. */
const GLYPH: Partial<Record<LiveState, string>> = {
  delayed: 'clock',
  stale: 'warning',
  closed: 'market-closed',
}

const TONE: Partial<Record<LiveState, string>> = {
  // Attention, in the chip colours the app keeps for it.
  delayed:
    'border-[color:var(--warning-fill)] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]',
  stale:
    'border-[color:var(--warning-fill)] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]',
  closed: 'border-border bg-card text-[color:var(--stale)]',
}

export function LiveStatusMarker({
  status,
  className,
}: {
  status: LiveStatus | null
  className?: string
}) {
  const glyph = status ? GLYPH[status.state] : undefined
  if (!status || !glyph) return null
  const detail = liveDetail(status)
  return (
    <output
      data-live-state={status.state}
      title={detail}
      className={cn(
        'pointer-events-auto inline-flex w-fit items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-semibold leading-4',
        TONE[status.state],
        className
      )}
    >
      <ChromeIcon id={glyph} size={12} />
      <span>{LIVE_LABEL[status.state]}</span>
      <span className="sr-only">. {detail}</span>
    </output>
  )
}
