import { ArrowDown, ArrowUp, Layers, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { directionOf } from '@/sudarshan/dashboard/format'
import { TrendArrow } from '@/sudarshan/dashboard/Trend'
import { type Outcome, outcomeOf } from './live'
import type { Side } from './types'

/**
 * LONG in the buy blue with an up arrow, SHORT in the sell red with a down arrow: the word and the
 * glyph carry the side, the fill is the second sign (design 6.2). White on both fills is above 4.5:1.
 */
export function SideChip({ side, className }: { side: Side; className?: string }) {
  const long = side === 'long'
  const Icon = long ? ArrowUp : ArrowDown
  return (
    <span
      data-side={side}
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-0.5 rounded-full px-2 text-[0.6875rem] font-bold tracking-[0.06em] text-white',
        long ? 'bg-[color:var(--buy-solid)]' : 'bg-[color:var(--sell-solid)]',
        className
      )}
    >
      <Icon aria-hidden="true" className="size-3" strokeWidth={3} />
      {long ? 'LONG' : 'SHORT'}
    </span>
  )
}

/** The strategy a trade belongs to, as a quiet chip with its icon; nothing for none. */
export function StrategyChip({ name, className }: { name: string; className?: string }) {
  if (!name) return null
  return (
    <span
      className={cn(
        'inline-flex h-6 max-w-[11rem] items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2 text-xs font-semibold text-[color:var(--text-secondary)]',
        className
      )}
    >
      <Layers aria-hidden="true" className="size-3 shrink-0" />
      <span className="truncate">{name}</span>
    </span>
  )
}

const OUTCOME_WORD: Record<Outcome, string> = { profit: 'Profit', loss: 'Loss', flat: 'Flat' }

/** The word of a result: read out beside the signed figure, and shown for a flat one. */
export function OutcomeWord({ paise, show = false }: { paise: number; show?: boolean }) {
  const word = OUTCOME_WORD[outcomeOf(paise)]
  return <span className={show ? 'text-xs font-semibold' : 'sr-only'}>{word}</span>
}

export { directionOf, TrendArrow }

/** A problem in a sentence with its next step, with an icon: never red alone. */
export function Problem({ children }: { children: React.ReactNode }) {
  return (
    <p
      role="alert"
      className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--down)]/40 bg-[color:var(--down-tint)] px-3 py-2 text-sm font-medium text-[color:var(--text-primary)]"
    >
      <TriangleAlert
        aria-hidden="true"
        className="mt-0.5 size-4 shrink-0 text-[color:var(--down)]"
      />
      <span>{children}</span>
    </p>
  )
}
