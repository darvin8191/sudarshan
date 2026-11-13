// SUDARSHAN P6 D4: the pieces the viewer's read-only boards share. Nothing here sends anything:
// a viewer's cards have no Exit, Add, Buy or Sell, and nothing opens an order ticket.
import { Clock, Info, X } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { istTime } from '@/sudarshan/books/time'
import type { Category, ReadState } from '@/sudarshan/family/types'
import { Hidden } from './Hidden'
import { Money, OUTLINED, StaleBookNote } from './parts'

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

/** "Positions from 10:41:05 · refresh on every order and every 45 s", in the quiet ink. */
export function ReadStamp({ part, readAt }: { part: string; readAt: number | null }) {
  if (readAt === null) return null
  return (
    <p className="flex items-center gap-1.5 px-1 text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
      <Clock aria-hidden="true" className="size-3.5 shrink-0" />
      {part} from {istTime(readAt)} · refresh on every order and every 45 s
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
