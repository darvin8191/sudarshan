// SUDARSHAN P6 D7a: small parts the Family page shares: a chip with an icon and a word (never
// colour alone), the one-send lock every write goes through, and the inline sentences.
import { Loader2, TriangleAlert } from 'lucide-react'
import { type ReactNode, useCallback, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { type OwnerError, ownerProblem } from '../ownerApi'

export const CARD =
  'rounded-[1.125rem] border bg-card px-4 py-4 shadow-[var(--shadow-card)] md:px-5'

export function Chip({
  icon,
  children,
  tone = 'plain',
}: {
  icon: ReactNode
  children: ReactNode
  tone?: 'plain' | 'brand' | 'good' | 'warn' | 'bad'
}) {
  const tones = {
    plain: 'bg-[color:var(--bg-canvas)] text-[color:var(--text-secondary)]',
    brand: 'bg-[color:var(--brand-tint)] text-[color:var(--brand)]',
    good: 'bg-[color:var(--up-tint)] text-[color:var(--up)]',
    warn: 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
    bad: 'bg-[color:var(--down-tint)] text-[color:var(--down)]',
  } as const
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.8125rem] font-semibold',
        tones[tone]
      )}
    >
      <span aria-hidden="true" className="flex shrink-0 items-center">
        {icon}
      </span>
      {children}
    </span>
  )
}

/** What a write said: the service's sentence, or the owner's words for a problem. */
export type Said = { kind: 'ok' | 'problem' | 'unconfirmed'; text: string }

/**
 * One send, however it is tapped. The lock is a ref, so two taps in the same frame (before React
 * has re-rendered the disabled button) still send once; it is let go when the answer comes, or
 * when the send throws. `run` returns undefined for a tap the lock turned away. A write that was
 * not answered says so ("Not confirmed"), never "failed", and the caller reads the page again.
 * `okText` may return a whole `Said`: a send that did part of what it set out to do resolves
 * with its data and says what is missing.
 */
export function useOnce(onSettled?: (said: Said, error: OwnerError | null) => void) {
  const busy = useRef(false)
  const [pending, setPending] = useState(false)
  const [said, setSaid] = useState<Said | null>(null)
  const run = useCallback(
    async <T,>(
      send: () => Promise<T>,
      okText: (data: T) => string | Said
    ): Promise<T | undefined> => {
      if (busy.current) return undefined
      busy.current = true
      setPending(true)
      setSaid(null)
      try {
        const data = await send()
        const words = okText(data)
        const next: Said = typeof words === 'string' ? { kind: 'ok', text: words } : words
        setSaid(next)
        onSettled?.(next, null)
        return data
      } catch (caught) {
        const error = ownerProblem(caught)
        const next: Said = {
          kind: error.unconfirmed ? 'unconfirmed' : 'problem',
          text: error.message,
        }
        setSaid(next)
        onSettled?.(next, error)
        return undefined
      } finally {
        busy.current = false
        setPending(false)
      }
    },
    [onSettled]
  )
  return { run, pending, said, clear: () => setSaid(null) }
}

/** The sentence a write left behind, spoken to a screen reader as it appears. */
export function SaidLine({ said }: { said: Said | null }) {
  if (!said) return null
  return (
    <output
      className={cn(
        'flex items-start gap-2 rounded-xl px-3 py-2 text-[0.9375rem] leading-snug',
        said.kind === 'ok'
          ? 'bg-[color:var(--up-tint)] text-[color:var(--up)]'
          : 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]'
      )}
    >
      {said.kind === 'ok' ? null : (
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      )}
      <span>{said.text}</span>
    </output>
  )
}

/** A destructive action asks first: the sentence, then Yes and No, each a 48 dp button. */
export function Ask({
  question,
  yes,
  no = 'Keep',
  pending,
  onYes,
  onNo,
}: {
  question: string
  yes: string
  no?: string
  pending: boolean
  onYes: () => void
  onNo: () => void
}) {
  return (
    <div
      role="alertdialog"
      aria-label={question}
      className="flex flex-col gap-3 rounded-xl border border-[color:var(--down)] bg-[color:var(--down-tint)] p-3"
    >
      <p className="flex items-start gap-2 text-[0.9375rem] leading-snug text-[color:var(--text-primary)]">
        <TriangleAlert
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-[color:var(--down)]"
        />
        {question}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="destructive"
          className="h-12 flex-1"
          disabled={pending}
          onClick={onYes}
        >
          {pending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
          {yes}
        </Button>
        <Button type="button" variant="outline" className="h-12 flex-1" onClick={onNo}>
          {no}
        </Button>
      </div>
    </div>
  )
}

/** A form control row with a visible label, at least 48 dp tall. */
export const ROW = 'flex min-h-12 items-center justify-between gap-3'

/**
 * A switch whose whole row is the tap target. Review M6 (v0.12.0): the switch alone is 44 x 24
 * and its name was 14 px high, a few pixels from the next row's, on switches that decide what a
 * family member sees. The row is the switch's `<label>`, so a tap anywhere on its 48 dp flips it
 * once; the switch keeps its own short name (the row's name, or `label`), and the line under it
 * is its description.
 */
export function SwitchRow({
  id,
  name,
  line,
  label,
  checked,
  disabled,
  onCheckedChange,
}: {
  id: string
  name: ReactNode
  line?: ReactNode
  /** The switch's spoken name where the row's name alone would not say enough. */
  label?: string
  checked: boolean
  disabled?: boolean
  onCheckedChange: (next: boolean) => void
}) {
  return (
    <label
      htmlFor={id}
      data-switch-row=""
      className={cn(ROW, 'w-full', disabled ? 'cursor-not-allowed' : 'cursor-pointer')}
    >
      <span className="flex min-w-0 flex-col gap-1 py-1.5">
        <span id={`${id}-name`} className="truncate text-sm leading-none font-medium">
          {name}
        </span>
        {line ? (
          <span id={`${id}-line`} className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
            {line}
          </span>
        ) : null}
      </span>
      <Switch
        id={id}
        checked={checked}
        disabled={disabled}
        aria-label={label}
        aria-labelledby={label ? undefined : `${id}-name`}
        aria-describedby={line ? `${id}-line` : undefined}
        onCheckedChange={onCheckedChange}
      />
    </label>
  )
}
