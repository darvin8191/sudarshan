import { Check, Flag, List, Lock, TrendingUp, Zap } from 'lucide-react'
import { type KeyboardEvent, useEffect } from 'react'
import type { Watchlist } from '@/api/watchlist'
import { cn } from '@/lib/utils'
import { PANEL_ID } from './ListTabs'
import type { Favourite, RefKind } from './v2/types'
import { LIMITS } from './v2/types'

/** One tab of the strip: a favourite, or the open list when it is not one. */
export interface StripTab {
  ref: string
  kind: RefKind
  name: string
  count: number | null
}

export const ownRef = (listId: number) => `list:${listId}`

/** The id of an own list in a ref, or null for a built-in, ranked or flag list. */
export function ownId(ref: string): number | null {
  const id = ref.startsWith('list:') ? Number(ref.slice(5)) : Number.NaN
  return Number.isInteger(id) && id > 0 ? id : null
}

/**
 * What the strip shows: the owner's favourites in slot order, up to seven; until he has pinned
 * any, his first seven lists in OpenAlgo's order (computed here, never stored as his choice).
 * An own list's name and count are read from the lists the page already holds, which are the
 * freshest; a favourite naming an own list that is gone is dropped.
 */
export function tabsFor(favourites: readonly Favourite[], own: readonly Watchlist[]): StripTab[] {
  const byRef = new Map(own.map((list) => [ownRef(list.id), list]))
  const tab = (ref: string, kind: RefKind, name: string, count: number | null): StripTab => {
    const list = byRef.get(ref)
    return list
      ? { ref, kind, name: list.name, count: list.items.length }
      : { ref, kind, name, count }
  }
  if (favourites.length === 0) {
    return own
      .slice(0, LIMITS.favourite_slots)
      .map((list) => tab(ownRef(list.id), 'list', list.name, list.items.length))
  }
  return [...favourites]
    .sort((a, b) => a.slot - b.slot)
    .filter((fav) => fav.kind !== 'list' || byRef.has(fav.ref))
    .slice(0, LIMITS.favourite_slots)
    .map((fav) => tab(fav.ref, fav.kind, fav.name, fav.count))
}

export const stripTabId = (ref: string) => `sd-strip-tab-${ref.replace(/\W/g, '-')}`

const KIND_WORDS: Partial<Record<RefKind, string>> = {
  preset: 'built-in, locked',
  ranked: 'ranked by Upstox',
  flag: 'flag list',
}

/**
 * The favourites as numbered tabs, up to seven, beside the All lists button, which stays in
 * view whatever the width while the tabs scroll sideways. The open tab is bold with a tick as
 * well as tinted. A list the owner opened from the sheet that is not a favourite is shown last
 * so the open list always has a tab. Arrow keys move between tabs.
 */
export function ListStrip({
  tabs,
  activeRef,
  fed,
  onSelect,
  onAll,
}: {
  tabs: readonly StripTab[]
  activeRef: string | null
  /** The own lists a screen feeds: each tab says Auto, an icon and a word. */
  fed?: ReadonlySet<number>
  onSelect: (ref: string) => void
  onAll: () => void
}) {
  useEffect(() => {
    if (activeRef === null) return
    const tab = document.getElementById(stripTabId(activeRef))
    if (tab && typeof tab.scrollIntoView === 'function') {
      tab.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }
  }, [activeRef])

  const step = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const count = tabs.length
    const to: Record<string, number> = {
      ArrowRight: (index + 1) % count,
      ArrowLeft: (index - 1 + count) % count,
      Home: 0,
      End: count - 1,
    }
    const target = tabs[to[event.key] ?? -1]
    if (!target) return
    event.preventDefault()
    onSelect(target.ref)
    document.getElementById(stripTabId(target.ref))?.focus()
  }

  return (
    <>
      <div
        role="tablist"
        aria-label="Favourite lists"
        className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab, index) => {
          const active = tab.ref === activeRef
          const listId = ownId(tab.ref)
          const Glyph = tab.kind === 'preset' ? Lock : tab.kind === 'ranked' ? TrendingUp : Flag
          return (
            <button
              key={tab.ref}
              id={stripTabId(tab.ref)}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={PANEL_ID}
              tabIndex={active ? 0 : -1}
              onClick={() => onSelect(tab.ref)}
              onKeyDown={(event) => step(event, index)}
              className="group flex h-12 shrink-0 items-center outline-none"
            >
              <span
                className={cn(
                  'flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
                  active
                    ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                    : 'border-border bg-card font-semibold text-[color:var(--text-secondary)]'
                )}
              >
                {active && <Check aria-hidden="true" className="size-3.5" />}
                {listId === null && <Glyph aria-hidden="true" className="size-3.5" />}
                <span className="max-w-[11rem] truncate">{tab.name}</span>
                {listId === null && KIND_WORDS[tab.kind] && (
                  <span className="sr-only">, {KIND_WORDS[tab.kind]}</span>
                )}
                {listId !== null && fed?.has(listId) && (
                  <span
                    data-auto="true"
                    className="inline-flex items-center gap-0.5 text-[0.6875rem] font-bold"
                  >
                    <Zap aria-hidden="true" className="size-3" />
                    Auto
                  </span>
                )}
                {tab.count !== null && (
                  <span className="text-xs font-semibold opacity-80">{tab.count}</span>
                )}
              </span>
            </button>
          )
        })}
      </div>
      <button
        type="button"
        onClick={onAll}
        className="flex h-12 shrink-0 items-center gap-1.5 rounded-xl px-2.5 text-sm font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      >
        <List aria-hidden="true" className="size-5" />
        All lists
      </button>
    </>
  )
}
