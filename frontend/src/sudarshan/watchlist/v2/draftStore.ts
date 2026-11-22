import { useEffect, useRef } from 'react'
import { istDay } from '@/sudarshan/dashboard/live'
import type { Draft } from './arrange'

/**
 * M8.4 GA6.4: the moves of edit mode that are not saved yet, kept while the owner leaves the page by
 * the bottom bar and restored when he comes back, the same day, in the same tab. One entry, in this
 * tab's session storage (a reload keeps it, another tab never sees it), mirrored in this module so
 * storage that is refused (a private window) still keeps it for the visit. Every access is guarded.
 *
 * It is written by `useArrange` on each change while a draft differs from what the list shows, and
 * dropped when the draft goes back to it, on Save order, on Discard moves, and when it cannot apply.
 */
export const DRAFT_KEY = 'sudarshan-watchlist-draft'

export interface KeptDraft {
  listId: number
  /** The list's `view.version` the draft stands on. */
  version: string
  /** The day in India it was made on (YYYY-MM-DD): a draft from an earlier day is never restored. */
  day: string
  draft: Draft
  base: Draft
}

let memory: KeptDraft | null = null

const isIds = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((each) => typeof each === 'number')

function isDraft(value: unknown): value is Draft {
  const d = value as Draft | null
  return (
    typeof d === 'object' &&
    d !== null &&
    isIds(d.sections) &&
    Array.isArray(d.rows) &&
    d.rows.every(
      (row) =>
        typeof row?.id === 'number' &&
        typeof row.exchange === 'string' &&
        typeof row.symbol === 'string' &&
        (row.section === null || typeof row.section === 'number')
    )
  )
}

function parse(raw: string): KeptDraft | null {
  try {
    const kept = JSON.parse(raw) as KeptDraft
    return typeof kept?.listId === 'number' &&
      typeof kept.version === 'string' &&
      typeof kept.day === 'string' &&
      isDraft(kept.draft) &&
      isDraft(kept.base)
      ? kept
      : null
  } catch {
    return null
  }
}

/** The kept draft, or null: storage's while it can be read, else the module's copy. */
export function readDraft(): KeptDraft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    return raw === null ? null : parse(raw)
  } catch {
    return memory // storage refused: the module's copy is all there is
  }
}

export function keepDraft(kept: KeptDraft): void {
  memory = kept
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(kept))
  } catch {
    try {
      sessionStorage.removeItem(DRAFT_KEY) // full: never leave an older draft to be read back
    } catch {
      // Refused: the module's copy keeps it for this visit.
    }
  }
}

/** Drop the kept draft; with `listId`, only when it is that list's. */
export function dropDraft(listId?: number): void {
  if (listId !== undefined && readDraft()?.listId !== listId) return
  memory = null
  try {
    sessionStorage.removeItem(DRAFT_KEY)
  } catch {
    // Storage refused: the module's copy is gone, which is all there was.
  }
}

/** The kept draft of `listId` when it can stand on the list as read now: same version, same day. */
export function draftFor(listId: number, version: string, now: number): KeptDraft | null {
  const kept = readDraft()
  return kept && kept.listId === listId && kept.version === version && kept.day === istDay(now)
    ? kept
    : null
}

export const droppedWords = (listName: string) =>
  `Your unsaved moves in ${listName} were dropped: the list was changed on another screen.`

/**
 * The page coming back to a draft it kept: once the lists and the switch are known, a draft for the
 * list that opens turns edit mode on again (`restore`), and `useArrange` starts from it. The list is
 * judged on a `view` read since the page opened (a cached one is read again first, once): one that
 * changed on another screen meanwhile drops the draft with `droppedWords`; a list gone, the switch
 * off, a draft of another list or of an earlier day are dropped without a word. Decided once a visit.
 */
export function useDraftReturn({
  settled,
  v2,
  listId,
  listIds,
  listName,
  view,
  reread,
  restore,
  dropped,
}: {
  /** The lists are read and the switch is known. */
  settled: boolean
  v2: boolean
  /** The list that opens. */
  listId: number | null
  listIds: readonly number[]
  listName: string
  /** That list's `view.version` and when it was read; undefined until it is. */
  view: { version: string; readAt: number } | undefined
  /** Read that list's `view` again. */
  reread: () => void
  restore: () => void
  dropped: (words: string) => void
}): void {
  const openedAt = useRef(Date.now())
  const decided = useRef(false)
  const asked = useRef(false)
  useEffect(() => {
    if (decided.current || !settled) return
    const kept = readDraft()
    if (kept === null) {
      decided.current = true
      return
    }
    const applies =
      v2 &&
      kept.day === istDay(Date.now()) &&
      kept.listId === listId &&
      listIds.includes(kept.listId)
    if (!applies) {
      decided.current = true
      dropDraft()
      return
    }
    if (view === undefined) return // the list's view is not read yet
    if (view.readAt < openedAt.current) {
      if (!asked.current) reread() // read before the page opened: it may not say what changed
      asked.current = true
      return
    }
    decided.current = true
    if (view.version === kept.version) restore()
    else {
      dropDraft()
      dropped(droppedWords(listName))
    }
  })
}
