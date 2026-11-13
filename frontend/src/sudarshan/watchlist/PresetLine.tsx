import type { QueryClient } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { WATCHLISTS_KEY } from './useWatchlists'
import { callOp } from './v2/api'
import { copiedWords, writeThenRead } from './v2/arrange'
import type { CopyDone } from './v2/types'
import { LISTS_KEY, viewKey } from './v2/useListView'

/**
 * M8.8 F10: what a built-in list adds over its rows. The server's own lines (the locked line, the
 * source and its date, the window line over 250 rows, the names the master lacks) are shown as
 * sent, each once, and Make a copy sits under them. Nothing here edits the list: it has no Add,
 * remove, reorder or sections to offer.
 */

/** The lines as sent, in order, each once (a repeated sentence is shown the one time). */
export function uniqueLines(lines: readonly string[]): string[] {
  return lines.filter((line, index) => line.trim() !== '' && lines.indexOf(line) === index)
}

/** The name the copy dialog starts with: "NIFTY 50 (my copy)", cut so it fits a list name (64). */
export function copyName(name: string): string {
  const tail = ' (my copy)'
  return `${[...name].slice(0, 64 - tail.length).join('')}${tail}`
}

export function PresetLine({
  lines,
  onCopy,
  empty,
}: {
  lines: readonly string[]
  /** Make a copy was tapped. */
  onCopy: () => void
  /** The list has no rows today: there is nothing to copy. */
  empty: boolean
}) {
  return (
    <div data-preset-lines="true" className="flex flex-col gap-1 px-1">
      {uniqueLines(lines).map((line) => (
        <p key={line} className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {line}
        </p>
      ))}
      <button
        type="button"
        disabled={empty}
        onClick={onCopy}
        className="inline-flex min-h-12 items-center gap-2 self-start rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
      >
        <Copy aria-hidden="true" className="size-5" />
        Make a copy
      </button>
    </div>
  )
}

/**
 * One `copy` of a built-in list into a new list: every key in the order the phone shows them (the
 * server cannot know a sort by price or change, and cuts at 250 itself, saying so in `words`).
 * The lists are read again whatever the answer. Throws the server's sentence when it refuses, so
 * the name dialog shows it and stays open.
 */
export async function copyPreset(
  client: QueryClient,
  ref: string,
  keys: readonly string[],
  name: string
): Promise<{ done: CopyDone; words: string }> {
  const out = await writeThenRead(client, [WATCHLISTS_KEY, LISTS_KEY, viewKey(ref)], () =>
    callOp('copy', { from: { ref, keys: [...keys] }, to: { name } })
  )
  if (!out.done) throw new Error(out.said ?? 'The copy could not be made. Try again.')
  return {
    done: out.done,
    words: out.done.words ?? copiedWords(false, out.done.added, out.done.skipped, name),
  }
}
