// SUDARSHAN P6 D4: the pieces the viewer's read-only boards share. Nothing here sends anything:
// a viewer's cards have no Exit, Add, Buy or Sell, and nothing opens an order ticket.
import { Clock, EyeOff, Info, X } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { istTime } from '@/sudarshan/books/time'
import { formatRupees } from '@/sudarshan/dashboard/format'
import type { Category, ReadState } from '@/sudarshan/family/types'
import { Hidden } from './Hidden'
import { Money, OUTLINED, STALE, StaleBookNote } from './parts'

/** What a viewer's board is given: rows as the service sent them, and where they stand. */
export interface ReadOnlyBookProps<Row> {
  readOnly: true
  rows: Row[]
  /** When the service read the broker, epoch ms. */
  readAt: number | null
  /** The categories the member's mask hides. */
  hidden: readonly Category[]
  /** The service's own state and sentence for the read ("Positions from 10:41:05. ..."). */
  state: ReadState
  words: string | null
  loading: boolean
  /** A read that failed just now, in the viewer's words. */
  error: string | null
  onRetry: () => void
  marketOpen: (exchange: string) => boolean | null
}

const NOTE_KEY = 'sd-family-note-'

function noteDismissed(name: string): boolean {
  try {
    return window.localStorage.getItem(NOTE_KEY + name) === '1'
  } catch {
    return false
  }
}

/**
 * The line that tells a viewer what this is, once per device: dismissible, and it stays dismissed.
 * Where storage is unavailable it simply shows each visit.
 */
export function ViewerNote({ name, text }: { name: string; text: string }) {
  const [gone, setGone] = useState(() => noteDismissed(name))
  if (gone) return null
  return (
    <aside className="flex items-start gap-2 rounded-[1rem] border bg-card px-3 py-2.5">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-[color:var(--info)]" />
      <p className="min-w-0 flex-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
        {text}
      </p>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          try {
            window.localStorage.setItem(NOTE_KEY + name, '1')
          } catch {
            // not kept: it shows again next visit
          }
          setGone(true)
        }}
        className="-my-1 -mr-1 flex size-12 shrink-0 items-center justify-center rounded-xl outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X aria-hidden="true" className="size-4" />
      </button>
    </aside>
  )
}

/**
 * "Positions from 10:41:05 · refresh on every order and every 45 s", in the quiet ink. While the
 * market is closed no timed read runs (review L4, v0.12.0), so `closed` leaves that half out.
 */
export function ReadStamp({
  part,
  readAt,
  closed = false,
}: {
  part: string
  readAt: number | null
  closed?: boolean
}) {
  if (readAt === null) return null
  return (
    <p className="flex items-center gap-1.5 px-1 text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
      <Clock aria-hidden="true" className="size-3.5 shrink-0" />
      {part} from {istTime(readAt)}
      {closed ? ' · market closed' : ' · refresh on every order and every 45 s'}
    </p>
  )
}

/**
 * What the service says about a read that is not fresh: its own sentence, as it gave it
 * ("The account cannot be read right now; prices still move. Figures from 10:41:05."). Nothing
 * is added: a sentence the service words is not reworded here.
 */
export function ServiceWords({ state, words }: { state: ReadState; words: string | null }) {
  if (state === 'ok' || !words) return null
  return <StaleBookNote text={words} />
}

const UNREAD_FALLBACK: Partial<Record<ReadState, string>> = {
  not_shared: 'Not shared with you.',
  portfolio_off: 'The account cannot be read right now. Ask the account owner.',
  unreachable: 'The account cannot be read right now. Try again in a minute.',
  budget: 'The account has not been read yet. Try again in a minute.',
}

/**
 * Review M4 (v0.12.0): a part that came with no rows because it was not read (not shared, the
 * portfolio switched off, the account out of reach, or never read yet) is not an empty account.
 * The sentence to show in place of "No open positions" and a zero: the service's own, or a plain
 * one where it sent none. Null when the rows, or their absence, are a real read.
 */
export function unreadWords(
  state: ReadState,
  words: string | null,
  rows: number,
  readAt: number | null
): string | null {
  if (rows > 0) return null
  const unread =
    state === 'not_shared' ||
    state === 'portfolio_off' ||
    state === 'unreachable' ||
    (state === 'budget' && readAt === null)
  if (!unread) return null
  return words?.trim() || UNREAD_FALLBACK[state] || null
}

/** The card for a part that was not read: the sentence as its title, an icon, and no figure. */
export function UnreadCard({ text }: { text: string }) {
  return (
    <output
      data-testid="book-unread"
      className="flex flex-col items-center gap-2 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]"
    >
      <EyeOff aria-hidden="true" className="size-5 text-[color:var(--text-secondary)]" />
      <span className="text-base font-bold">{text}</span>
    </output>
  )
}

/** A figure that may be hidden: Hidden where the mask removed it, a dash where not known. */
export function Figure({
  value,
  stale = false,
  className,
}: {
  value: number | null | undefined
  stale?: boolean
  className?: string
}) {
  if (value === undefined) return <Hidden className={className} />
  return <Money value={value} stale={stale} className={className} />
}

/**
 * Money without a sign, an arrow or a trend colour, where a figure is a size and not a gain: what
 * something is worth, what went into it. Review M2 (v0.12.0): a portfolio's value drawn as
 * "▲ +₹2,27,06,125.00" in profit green read as a profit of that size.
 */
export function Plain({
  value,
  stale = false,
  className,
}: {
  value: number | null | undefined
  stale?: boolean
  className?: string
}) {
  if (value === undefined) return <Hidden className={className} />
  if (value === null) return <span className={cn('text-muted-foreground', className)}>-</span>
  return (
    <span data-plain="" className={cn(stale && STALE, className)}>
      {formatRupees(value)}
    </span>
  )
}

/** The row's price slot when its price is not coming live: the book's price, with its time. */
export function NotRealTime({ readAt }: { readAt: number | null }) {
  return (
    <span className="flex items-center gap-1 text-xs font-semibold text-[color:var(--warning-text)]">
      <Clock aria-hidden="true" className="size-3.5 shrink-0" />
      Not real-time · too many symbols open
      {readAt !== null ? ` · price from ${istTime(readAt)}` : ''}
    </span>
  )
}

/** A big "Hidden" where a total would be. */
export function HiddenTotal({ className }: { className?: string }) {
  return <Hidden className={cn('text-lg font-bold', className)} />
}

export { OUTLINED }
