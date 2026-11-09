import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Flag, Lock, Plus, Star, TrendingUp } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import { ownId } from './ListStrip'
import { callOp } from './v2/api'
import type { Entry, Favourite, FavSlot } from './v2/types'
import { LIMITS } from './v2/types'
import { LISTS_KEY, STATE_KEY, useLists, useV2State } from './v2/useListView'

export const FAVOURITES_FULL = 'Seven favourites at most. Remove one first.'
export const PICK_HINT = 'Pick your favourites here. They show as numbered tabs.'
const ICON_BUTTON =
  'flex size-12 shrink-0 items-center justify-center rounded-xl outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40'

const KIND_WORDS = {
  list: 'Your list',
  preset: 'Built-in, locked',
  ranked: 'Ranked by Upstox',
  flag: 'Flag list',
} as const

function Block({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col">
      <h3 className="px-1 pt-3 pb-1 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
        {title}
        {note && (
          <span className="ml-2 font-semibold text-[color:var(--text-tertiary)]">{note}</span>
        )}
      </h3>
      <ul className="flex flex-col divide-y divide-[color:var(--divider)] rounded-[0.875rem] border bg-card">
        {children}
      </ul>
    </section>
  )
}

/**
 * Every list of the owner on one sheet (`?sheet=lists`), in blocks: his favourites (the tabs, in
 * order), his own lists, the lists a screen feeds, the built-in lists (locked), the ranked lists
 * from Upstox and the flag lists. A list's kind is said in words, never by an icon alone. The
 * star pins a list as a favourite or removes it, seven at most; the sheet ignores taps for the
 * first half second (BottomSheet).
 */
export function AllListsSheet({
  open,
  onClose,
  fed,
  activeRef,
  onPick,
  onNew,
  newOff,
}: {
  open: boolean
  onClose: () => void
  fed: ReadonlySet<number>
  activeRef: string | null
  onPick: (ref: string) => void
  onNew: () => void
  /** Why New list is off, or null. */
  newOff: string | null
}) {
  const client = useQueryClient()
  const lists = useLists(open)
  const version = useV2State().data?.versions?.favourites ?? 0
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const data = lists.data
  const pinned: Favourite[] = [...(data?.favourites ?? [])].sort((a, b) => a.slot - b.slot)
  const pinnedRefs = new Set(pinned.map((fav) => fav.ref))
  const full = pinned.length >= LIMITS.favourite_slots

  const save = async (refs: string[]) => {
    setBusy(true)
    setProblem(null)
    const slots: FavSlot[] = refs.map((ref, index) => ({ slot: index + 1, ref }))
    try {
      await callOp('favourites', { slots, version })
    } catch (error) {
      setProblem(error instanceof Error ? error.message : 'The favourites could not be saved.')
    } finally {
      // Read again either way: after a refusal the lists are the other screen's.
      await Promise.all([
        client.invalidateQueries({ queryKey: STATE_KEY }),
        client.invalidateQueries({ queryKey: LISTS_KEY }),
      ])
      setBusy(false)
    }
  }
  const refs = pinned.map((fav) => fav.ref)
  const shift = (index: number, by: -1 | 1) => {
    const next = [...refs]
    ;[next[index], next[index + by]] = [next[index + by], next[index]]
    void save(next)
  }

  const row = (entry: Pick<Entry, 'ref' | 'kind' | 'name' | 'count'>, extra?: ReactNode) => {
    const on = pinnedRefs.has(entry.ref)
    const Glyph = entry.kind === 'preset' ? Lock : entry.kind === 'ranked' ? TrendingUp : Flag
    const words = fed.has(ownId(entry.ref) ?? 0) ? 'Fed by a screen' : KIND_WORDS[entry.kind]
    return (
      <li key={entry.ref} className="flex min-h-14 items-center">
        <button
          type="button"
          onClick={() => onPick(entry.ref)}
          aria-current={entry.ref === activeRef || undefined}
          className="flex min-h-14 min-w-0 flex-1 flex-col justify-center px-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span
            className={cn(
              'truncate text-[0.9375rem]',
              entry.ref === activeRef ? 'font-bold' : 'font-semibold'
            )}
          >
            {entry.name}
            {entry.ref === activeRef && <span className="sr-only"> (open)</span>}
          </span>
          <span className="flex items-center gap-1 text-xs font-medium text-[color:var(--text-tertiary)]">
            {entry.kind !== 'list' && <Glyph aria-hidden="true" className="size-3" />}
            {words}
            {entry.count !== null && <span>, {entry.count}</span>}
          </span>
        </button>
        {extra}
        <button
          type="button"
          aria-pressed={on}
          aria-label={`${on ? 'Remove from favourites' : 'Add to favourites'}: ${entry.name}`}
          disabled={busy || (!on && full)}
          onClick={() =>
            void save(on ? refs.filter((ref) => ref !== entry.ref) : [...refs, entry.ref])
          }
          className={cn(ICON_BUTTON, on ? 'text-primary' : 'text-[color:var(--text-secondary)]')}
        >
          <Star aria-hidden="true" className="size-5" fill={on ? 'currentColor' : 'none'} />
        </button>
      </li>
    )
  }

  const own = (data?.own ?? []).filter((entry) => !fed.has(ownId(entry.ref) ?? 0))
  const feeds = (data?.own ?? []).filter((entry) => fed.has(ownId(entry.ref) ?? 0))
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title="All lists"
      description={`${pinned.length} of ${LIMITS.favourite_slots} favourites`}
    >
      {lists.isPending ? (
        <p className="py-6 text-center text-sm font-medium">Reading your lists...</p>
      ) : lists.isError || !data ? (
        <p role="alert" className="py-6 text-center text-sm font-semibold">
          {lists.error?.message ?? 'The lists could not be read.'}
        </p>
      ) : (
        <div className="flex flex-col">
          <button
            type="button"
            disabled={newOff !== null}
            onClick={onNew}
            className="flex min-h-12 items-center gap-2 rounded-xl px-2 text-[0.9375rem] font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <Plus aria-hidden="true" className="size-5" />
            New list
          </button>
          {newOff && <p className="px-2 text-xs font-medium text-muted-foreground">{newOff}</p>}
          {problem && (
            <p role="alert" className="px-1 py-2 text-sm font-semibold text-[color:var(--down)]">
              {problem}
            </p>
          )}
          {pinned.length === 0 ? (
            <p className="px-1 pt-3 text-sm font-medium text-[color:var(--text-secondary)]">
              {PICK_HINT}
            </p>
          ) : (
            <Block title="Favourites" note={`${pinned.length} of ${LIMITS.favourite_slots}`}>
              {pinned.map((fav, index) =>
                row(
                  fav,
                  <>
                    <button
                      type="button"
                      aria-label={`Move up: ${fav.name}`}
                      disabled={busy || index === 0}
                      onClick={() => shift(index, -1)}
                      className={ICON_BUTTON}
                    >
                      <ChevronUp aria-hidden="true" className="size-5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Move down: ${fav.name}`}
                      disabled={busy || index === pinned.length - 1}
                      onClick={() => shift(index, 1)}
                      className={ICON_BUTTON}
                    >
                      <ChevronDown aria-hidden="true" className="size-5" />
                    </button>
                  </>
                )
              )}
            </Block>
          )}
          {full && (
            <p className="px-1 pt-1 text-xs font-medium text-muted-foreground">{FAVOURITES_FULL}</p>
          )}
          {own.length > 0 && <Block title="Your lists">{own.map((entry) => row(entry))}</Block>}
          {feeds.length > 0 && (
            <Block title="Fed by a screen">{feeds.map((entry) => row(entry))}</Block>
          )}
          {data.presets.length > 0 && (
            <Block title="Built-in (locked)">{data.presets.map((entry) => row(entry))}</Block>
          )}
          {data.ranked.length > 0 && (
            <Block title="Ranked by Upstox">{data.ranked.map((entry) => row(entry))}</Block>
          )}
          {data.flags.length > 0 && (
            <Block title="Flags">{data.flags.map((entry) => row(entry))}</Block>
          )}
        </div>
      )}
    </BottomSheet>
  )
}
