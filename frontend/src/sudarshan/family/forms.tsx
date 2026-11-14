// SUDARSHAN P6 D3: the small form pieces the family's public pages and Settings share. Plain
// elements at the platform's 48 dp target, with the service's own sentence under a field.
import { AlertCircle, Info } from 'lucide-react'
import { type ReactNode, useId } from 'react'
import { cn } from '@/lib/utils'

export const CARD =
  'flex flex-col gap-3 rounded-[1.125rem] border bg-card px-4 py-4 shadow-[var(--shadow-card)]'

const INPUT =
  'min-h-12 w-full min-w-0 rounded-[0.875rem] border border-input bg-card px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring aria-invalid:border-destructive'

const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60'

export const PRIMARY = cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')
export const OUTLINE = cn(BUTTON, 'border border-input bg-card text-foreground hover:bg-accent')
export const DANGER = cn(
  BUTTON,
  'border border-destructive bg-card text-destructive hover:bg-[color:var(--down-tint)]'
)

/** A labelled input. The hint and the sentence about it are tied to it for a screen reader. */
export function Field({
  label,
  hint,
  error,
  ...input
}: {
  label: string
  hint?: string
  error?: string | null
} & Omit<React.ComponentProps<'input'>, 'className'>) {
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const described = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ')
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-semibold text-[color:var(--text-secondary)]">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={described || undefined}
        className={INPUT}
        {...input}
      />
      {hint && (
        <p id={hintId} className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          {hint}
        </p>
      )}
      {error && <Problem id={errorId} text={error} />}
    </div>
  )
}

/** A sentence about what went wrong: an icon and the words, never colour alone. */
export function Problem({ text, id }: { text: string; id?: string }) {
  return (
    <p
      id={id}
      role="alert"
      className="flex items-start gap-2 text-[0.8125rem] font-semibold text-[color:var(--down)]"
    >
      <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{text}</span>
    </p>
  )
}

/** Something the member should know that is not their mistake: an icon and the words. */
export function Notice({ text }: { text: string }) {
  return (
    <output
      data-testid="family-notice"
      className="flex w-full items-start gap-2 rounded-xl bg-[color:var(--gold-tint)] px-3 py-2.5 text-[0.9375rem] leading-snug font-semibold text-[color:var(--warning-text)]"
    >
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{text}</span>
    </output>
  )
}

/** A line of good news after a save. */
export function Saved({ text }: { text: string }) {
  return (
    <output className="block text-[0.8125rem] font-semibold text-[color:var(--up)]">{text}</output>
  )
}

export function Section({
  title,
  children,
  id,
}: {
  title: string
  children: ReactNode
  id?: string
}) {
  const heading = useId()
  return (
    <section id={id} aria-labelledby={heading} className={CARD}>
      <h2 id={heading} className="text-base font-bold">
        {title}
      </h2>
      {children}
    </section>
  )
}
