import { cn } from '@/lib/utils'
import type { Direction } from './format'

/** The up and down colours (blue and orange in colour-blind mode), ink when flat. */
export const TREND_TEXT: Record<Direction, string> = {
  up: 'text-[color:var(--up)]',
  down: 'text-[color:var(--down)]',
  flat: 'text-foreground',
}

/**
 * The solid triangle that goes with a signed figure, so a direction is told by
 * shape and sign as well as colour. Nothing for a flat figure. Decorative: the
 * sign in the text is what a screen reader reads.
 */
export function TrendArrow({ direction, className }: { direction: Direction; className?: string }) {
  if (direction === 'flat') return null
  return (
    <svg
      viewBox="0 0 10 10"
      aria-hidden="true"
      focusable="false"
      data-direction={direction}
      className={cn('shrink-0', className)}
    >
      <path
        d={direction === 'up' ? 'M5 1.5 9.2 8.5H0.8z' : 'M5 8.5 0.8 1.5h8.4z'}
        fill="currentColor"
      />
    </svg>
  )
}
