import { Switch } from '@/components/ui/switch'

const IST_SECOND = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

/** "10:42:05": a moment in India, to the second. */
export function clockTime(at: number | Date): string {
  return IST_SECOND.format(at)
}

/** "Read again every 30 s (not real-time)": what a tool's timer switch says. */
export function timedLabel(everyMs: number): string {
  const seconds = Math.round(everyMs / 1000)
  const every = seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`
  return `Read again every ${every} (not real-time)`
}

/**
 * The timer switch of a wide-screen tool that reads over REST (M11.5, T6): off unless switched on,
 * and it says what it is. These pages are not on ticks, so the switch never says "auto" or "live",
 * and the page shows when its figures were read.
 */
export function TimedRefresh({
  id,
  everyMs,
  on,
  onChange,
  updatedAt,
  disabled = false,
}: {
  id: string
  everyMs: number
  on: boolean
  onChange: (on: boolean) => void
  /** When the figures on the page were read; null before the first read. */
  updatedAt: number | null
  disabled?: boolean
}) {
  return (
    <div
      data-timed-refresh={on ? 'on' : 'off'}
      className="flex flex-wrap items-center gap-x-3 gap-y-1"
    >
      <span className="flex min-h-12 items-center gap-2">
        <Switch id={id} checked={on} onCheckedChange={onChange} disabled={disabled} />
        <label htmlFor={id} className="cursor-pointer text-sm">
          {timedLabel(everyMs)}
        </label>
      </span>
      {updatedAt !== null && (
        <span data-updated-at="true" className="text-xs font-medium text-muted-foreground">
          Updated {clockTime(updatedAt)}
        </span>
      )}
    </div>
  )
}
