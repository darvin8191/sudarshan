import { cn } from '@/lib/utils'

/**
 * The five marks (M8.4 F4): a shape and a colour each, drawn by sections now and by flags later.
 * A mark is never shown without its name beside it, and the colour-blind mode changes nothing
 * here: the shape and the name carry the difference. The colours are the `--mark-n` tokens
 * (theme.css, pinned in theme.test.ts). Mark 0 is no mark.
 */
export type MarkId = 1 | 2 | 3 | 4 | 5

export const MARK_IDS: readonly MarkId[] = [1, 2, 3, 4, 5]

export type MarkShape = 'circle' | 'square' | 'triangle' | 'diamond' | 'star'

export const MARKS: Record<MarkId, { shape: MarkShape; name: string; color: string }> = {
  1: { shape: 'circle', name: 'Circle', color: 'var(--mark-1)' },
  2: { shape: 'square', name: 'Square', color: 'var(--mark-2)' },
  3: { shape: 'triangle', name: 'Triangle', color: 'var(--mark-3)' },
  4: { shape: 'diamond', name: 'Diamond', color: 'var(--mark-4)' },
  5: { shape: 'star', name: 'Star', color: 'var(--mark-5)' },
}

/** The mark a number names, or null for 0 (none) and anything out of range. */
export function markOf(mark: number): (typeof MARKS)[MarkId] | null {
  return mark >= 1 && mark <= 5 ? MARKS[mark as MarkId] : null
}

const PATHS: Record<MarkShape, string> = {
  circle: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18Z',
  square: 'M4 4h16v16H4Z',
  triangle: 'M12 3.5 21.5 20h-19Z',
  diamond: 'M12 2.5 21.5 12 12 21.5 2.5 12Z',
  star: 'm12 2.5 2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.7l-6.1 3.4 1.4-6.8-5.1-4.7 6.9-.8Z',
}

/** The glyph of a mark, filled in its colour; decorative, since the name is always beside it. */
export function MarkGlyph({ mark, className }: { mark: number; className?: string }) {
  const look = markOf(mark)
  if (!look) return null
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      data-mark={mark}
      data-shape={look.shape}
      viewBox="0 0 24 24"
      className={cn('size-4 shrink-0', className)}
      style={{ color: look.color }}
    >
      <path d={PATHS[look.shape]} fill="currentColor" />
    </svg>
  )
}
