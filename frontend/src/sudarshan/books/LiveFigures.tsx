import { cn } from '@/lib/utils'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import type { TickDirection } from './prices'

/**
 * A last traded price as a broker app shows it (M6.2): the colour and the
 * triangle of the last tick's direction, up or down, in the theme's up and
 * down tokens (blue and orange in colour-blind mode). No background flash and
 * no animation, by the D look's rule of minimal motion. A price whose ticks
 * have stopped is greyed and keeps its triangle, so it never looks current.
 */
export function TickPrice({
  value,
  direction,
  stale = false,
  format,
  className,
}: {
  value: number | undefined | null
  direction: TickDirection | null | undefined
  stale?: boolean
  format: (value: number) => string
  className?: string
}) {
  if (typeof value !== 'number' || !Number.isFinite(value))
    return <span className={className}>-</span>
  const tone = stale ? 'text-muted-foreground' : direction ? TREND_TEXT[direction] : undefined
  return (
    <span
      className={cn('inline-flex items-center justify-end gap-1 tabular-nums', tone, className)}
      data-tick={direction ?? undefined}
      data-stale={stale || undefined}
    >
      {direction && <TrendArrow direction={direction} className="size-2.5" />}
      {format(value)}
      {direction && (
        <span className="sr-only">{direction === 'up' ? ' last tick up' : ' last tick down'}</span>
      )}
      {stale && <span className="sr-only"> stale</span>}
    </span>
  )
}
