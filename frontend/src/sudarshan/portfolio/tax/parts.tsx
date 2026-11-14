// SUDARSHAN P7 F1 (M7.27): the small pieces every card of the tax view is built from. Barlow with
// tabular figures in every row; Khand (`sd-display`) only on a card's one big figure.
import { CircleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Signed } from '../Signed'

export const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
export const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60'
export const OUTLINE = `${BUTTON} border border-[color:var(--border-strong)] bg-card text-[color:var(--brand)] hover:bg-accent`

export function Card({
  title,
  big,
  bigLabel,
  children,
}: {
  title: string
  /** The card's one big figure, in Khand. */
  big?: ReactNode
  bigLabel?: string
  children: ReactNode
}) {
  return (
    <section aria-label={title} className={cn(CARD, 'flex flex-col gap-2')}>
      <h2 className="text-xs font-semibold text-muted-foreground">{title}</h2>
      {big !== undefined && (
        <div className="flex flex-col">
          <span className="sd-display text-[1.75rem] leading-tight font-bold break-words">
            {big}
          </span>
          {bigLabel && (
            <span className="text-xs text-[color:var(--text-tertiary)]">{bigLabel}</span>
          )}
        </div>
      )}
      {children}
    </section>
  )
}

/** A label and its figure: the label wraps, the figure never breaks into two amounts. */
export function Line({
  label,
  sub,
  children,
  strong = false,
}: {
  label: ReactNode
  sub?: ReactNode
  children: ReactNode
  strong?: boolean
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="flex min-w-0 flex-col text-sm text-[color:var(--text-secondary)]">
        <span className={cn(strong && 'font-semibold text-foreground')}>{label}</span>
        {sub && <span className="text-xs text-[color:var(--text-tertiary)]">{sub}</span>}
      </span>
      <span
        className={cn(
          'shrink-0 text-right text-[0.9375rem] whitespace-nowrap',
          strong && 'font-bold'
        )}
      >
        {children}
      </span>
    </div>
  )
}

/** A result that can go either way: sign, arrow and the word, never the colour alone. */
export function Result({ paise, className }: { paise: number; className?: string }) {
  const word = paise > 0 ? 'Gain' : paise < 0 ? 'Loss' : 'Nil'
  return (
    <span className={cn('inline-flex flex-wrap items-baseline justify-end gap-x-1.5', className)}>
      <Signed paise={paise} />
      <span className="text-xs font-semibold text-[color:var(--text-secondary)]">{word}</span>
    </span>
  )
}

/** Sentences the service wrote, one to a line; `warn` gives each a warning icon. */
export function Sentences({ lines, warn = false }: { lines: readonly string[]; warn?: boolean }) {
  if (lines.length === 0) return null
  return (
    <ul className="flex flex-col gap-1.5">
      {lines.map((line) => (
        <li key={line} className="flex gap-2 text-sm text-[color:var(--text-secondary)]">
          {warn && (
            <CircleAlert
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
            />
          )}
          <span className="min-w-0">{line}</span>
        </li>
      ))}
    </ul>
  )
}

export const RULE = 'border-t border-[color:var(--divider)] pt-2'
