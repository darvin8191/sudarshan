// SUDARSHAN M13 (P8): a bias as TRINETRA publishes it, told three ways: an arrow, its word and, while
// the reading is recent enough (`colour`), the up or down token (ink for neutral). Never colour
// alone; past 30 minutes of age, or on a lapsed reading, the word and arrow stay and the colour goes.
import { cn } from '@/lib/utils'
import type { Bias } from './types'
import { biasWord } from './words'

const TONE: Record<Bias, string> = {
  bull: 'text-[color:var(--up)]',
  bear: 'text-[color:var(--down)]',
  neutral: 'text-[color:var(--text-primary)]',
}

const DIRECTION: Record<Bias, 'up' | 'down' | 'flat'> = {
  bull: 'up',
  bear: 'down',
  neutral: 'flat',
}

/** Up and down as the books' triangles; neutral as an arrow along the line. Decorative. */
export function BiasArrow({ bias, className }: { bias: Bias; className?: string }) {
  const direction = DIRECTION[bias] ?? 'flat'
  return (
    <svg
      viewBox="0 0 10 10"
      aria-hidden="true"
      focusable="false"
      data-direction={direction}
      className={cn('size-2.5 shrink-0', className)}
    >
      {direction === 'flat' ? (
        <path d="M0.8 4.2h5.4V1.8L9.4 5 6.2 8.2V5.8H0.8z" fill="currentColor" />
      ) : (
        <path
          d={direction === 'up' ? 'M5 1.5 9.2 8.5H0.8z' : 'M5 8.5 0.8 1.5h8.4z'}
          fill="currentColor"
        />
      )}
    </svg>
  )
}

export function BiasMark({
  bias,
  colour,
  className,
}: {
  bias: Bias
  /** A colour of bias may be shown (the freshness rule, and not lapsed). */
  colour: boolean
  className?: string
}) {
  return (
    <span
      data-bias={bias}
      className={cn(
        'inline-flex shrink-0 items-center gap-1 font-bold',
        colour ? (TONE[bias] ?? TONE.neutral) : 'text-[color:var(--text-secondary)]',
        className
      )}
    >
      <BiasArrow bias={bias} />
      {biasWord(bias)}
    </span>
  )
}
