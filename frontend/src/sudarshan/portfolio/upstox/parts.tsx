// SUDARSHAN M7.1-M7.4: the pieces every part of the Upstox tab shares.
import { Clock, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { clockOfInstant } from '../days'
import type { EpochMs, UpstoxPart } from '../types'

export const CARD = 'rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]'
export const CHIP =
  'inline-flex min-h-6 items-center rounded-full border border-[color:var(--border-strong)] px-2 text-xs font-semibold text-[color:var(--text-secondary)]'
export const LINK =
  'inline-flex min-h-12 items-center gap-1.5 rounded-lg px-2 text-[0.9375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring'

/** "Read 10:41:05": when the service read this part from Upstox. */
export function ReadStamp({ at }: { at: EpochMs | null }) {
  if (at === null) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold whitespace-nowrap text-[color:var(--text-tertiary)]">
      <Clock aria-hidden="true" className="size-3.5 shrink-0" />
      Read {clockOfInstant(at)}
    </span>
  )
}

/** The sentence the service wrote for a part that is slow, refused or closed, as it wrote it. */
export function PartNote({ part }: { part: UpstoxPart<unknown> | undefined }) {
  if (!part || !part.note) return null
  return (
    <p
      aria-live="polite"
      className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--warning-text)] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-medium text-[color:var(--warning-text)]"
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      {part.note}
    </p>
  )
}

/** A part with no figures at all: the note says why, and no zero stands in its place. */
export function NoFigures({
  part,
  children,
}: {
  part: UpstoxPart<unknown> | undefined
  children: ReactNode
}) {
  return (
    <section className={cn(CARD, 'px-4 py-6 text-center')} aria-live="polite">
      <p className="text-base font-bold">{part?.note ?? children}</p>
    </section>
  )
}

/** A label above a figure, as the dashboard's cards do. */
export function Figure({
  label,
  children,
  note,
}: {
  label: string
  children: ReactNode
  note?: ReactNode
}) {
  return (
    <div className="flex min-w-0 flex-col gap-px">
      <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
      <dd className="sd-display text-[1.3125rem] leading-[1.15] font-semibold">{children}</dd>
      {note && (
        <dd className="text-[0.71875rem] font-medium text-[color:var(--text-tertiary)]">{note}</dd>
      )}
    </div>
  )
}
