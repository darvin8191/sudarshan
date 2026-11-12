import type { QueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { problemOf } from './api'
import { LIMITS, type Section, type View } from './types'
import { viewKey } from './useListView'

/** The words of the section rules, the same before sending and when the server refuses. */
export const NAME_LONG = `Section names can be up to ${LIMITS.section_name} letters.`
export const SECTIONS_FULL = `A list can have ${LIMITS.sections_per_list} sections.`

export function removeWords(section: Pick<Section, 'name' | 'count'>): string {
  const { name, count } = section
  if (count === 0) return `Remove the section ${name}? It has no symbols.`
  return `Remove the section ${name}? Its ${count} ${count === 1 ? 'symbol stays' : 'symbols stay'} in the list, under No section.`
}

/** The rows of a view in the order they are shown: no section first, then each section. */
export interface Group {
  section: Section | null
  /** The OpenAlgo item ids, in the view's order. */
  ids: number[]
}

export function groupsOf(view: View): Group[] {
  const bySection = new Map<number | null, number[]>()
  for (const row of view.rows) {
    if (row.item === null) continue
    const key =
      row.section_id !== null && view.sections.some((s) => s.id === row.section_id)
        ? row.section_id
        : null
    bySection.set(key, [...(bySection.get(key) ?? []), row.item])
  }
  const sections = [...view.sections].sort((a, b) => a.position - b.position)
  return [
    { section: null, ids: bySection.get(null) ?? [] },
    ...sections.map((section) => ({ section, ids: bySection.get(section.id) ?? [] })),
  ]
}

/** Which sections are folded, per list, on this device only: a convenience, so a refused store is ignored. */
export const COLLAPSED_KEY = 'sudarshan-watchlist-collapsed'

function readCollapsed(ref: string): number[] {
  try {
    const all = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '{}') as Record<string, unknown>
    const ids = all[ref]
    return Array.isArray(ids) ? ids.filter((id): id is number => Number.isInteger(id)) : []
  } catch {
    return []
  }
}

function writeCollapsed(ref: string, ids: number[]): void {
  try {
    const all = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '{}') as Record<string, unknown>
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify({ ...all, [ref]: ids }))
  } catch {
    // Storage refused (a private window): the fold lasts for the visit.
  }
}

/** The folded sections of the list `ref`, and the toggle that remembers a change. */
export function useCollapsed(ref: string | null) {
  const [held, setHeld] = useState<{ ref: string | null; ids: number[] }>(() => ({
    ref,
    ids: ref ? readCollapsed(ref) : [],
  }))
  // Another list opened: read its folds (the state is set while rendering, as React allows).
  if (held.ref !== ref) setHeld({ ref, ids: ref ? readCollapsed(ref) : [] })
  const collapsed = useMemo(() => new Set(held.ids), [held.ids])
  const toggle = useCallback(
    (id: number) => {
      if (!ref) return
      const ids = held.ids.includes(id) ? held.ids.filter((each) => each !== id) : [...held.ids, id]
      setHeld({ ref, ids })
      writeCollapsed(ref, ids)
    },
    [ref, held.ids]
  )
  return { collapsed, toggle }
}

/**
 * One section write, sent with the version `view` answered. The list is read again whatever the
 * answer (after a conflict the list is the other screen's). Resolves to the sentence to show, or
 * null when it was done; a refusal never shows a status code.
 */
export async function writeSection(
  client: QueryClient,
  view: View,
  send: (version: string) => Promise<unknown>
): Promise<string | null> {
  let said: string | null = null
  try {
    await send(view.version)
  } catch (error) {
    const problem = problemOf(error)
    said = problem.code === 'limit' ? SECTIONS_FULL : problem.message
  }
  await client.invalidateQueries({ queryKey: viewKey(view.ref) })
  return said
}
