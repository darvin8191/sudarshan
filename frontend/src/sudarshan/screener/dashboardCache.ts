/**
 * The Dashboard's cached read as plain functions (design 3.7): a card is (screen id, kind), never
 * an id, so a suggested layout at version 0 is addressed the same way as a stored one.
 */
import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { ScreenerError, screenerApi } from './api'
import { DASHBOARD_KEY, widgetKey } from './keys'
import type { Dashboard, RunPush, RunRow, WidgetCard } from './types'

export const cardKey = (card: Pick<WidgetCard, 'screen_id' | 'kind'>) =>
  `${card.screen_id}:${card.kind}`

/** The run the rows a card shows were read at: the pushed run may be ahead of them. */
export const rowsRun = (card: Pick<WidgetCard, 'run' | 'read_run'>) =>
  card.read_run !== undefined ? card.read_run : card.run

/** True while a card's rows are older than the run its line names. */
export const rowsBehind = (card: Pick<WidgetCard, 'run' | 'read_run'>) =>
  card.read_run !== undefined && (card.run?.id ?? 0) > (card.read_run?.id ?? 0)

/**
 * A card read alone takes the place of the one held, in the same position, unless the rows held
 * are of a newer run than the read's (a read that started before a run answers after it).
 */
export function putCard(held: Dashboard, card: WidgetCard): Dashboard {
  return {
    ...held,
    widgets: held.widgets.map((each) => {
      if (cardKey(each) !== cardKey(card)) return each
      return newerHeld(each, card) ? each : card
    }),
  }
}

function newerHeld(held: WidgetCard, read: WidgetCard): boolean {
  if (held.rows_pending || !read.run) return false
  return (rowsRun(held)?.id ?? 0) > read.run.id
}

/**
 * The dashboard read lays the cards out, but a card the page holds with rows of a newer run than
 * the read has (a run landed while the read was in flight) is kept: an older read never replaces
 * a newer card.
 */
export function mergeRead(held: Dashboard | undefined, read: Dashboard): Dashboard {
  if (!held) return read
  const have = new Map(held.widgets.map((card) => [cardKey(card), card]))
  return {
    ...read,
    last_run_id: Math.max(read.last_run_id, held.last_run_id),
    widgets: read.widgets.map((card) => {
      const mine = have.get(cardKey(card))
      return mine && newerHeld(mine, card) ? { ...mine, position: card.position } : card
    }),
  }
}

/**
 * Cards whose own read failed after a run was pushed (design 3.7): they say so, with Try again,
 * rather than show an earlier check's rows under the new run line. Kept outside the query cache
 * because it is a fact about this phone's reads, not data; cleared when the dashboard is read.
 */
let unread: ReadonlySet<string> = new Set()
const unreadListeners = new Set<() => void>()

function setUnread(next: ReadonlySet<string>) {
  unread = next
  for (const listener of [...unreadListeners]) listener()
}

export const isUnread = (key: string) => unread.has(key)
export const markUnread = (key: string) => {
  if (!unread.has(key)) setUnread(new Set(unread).add(key))
}
export const clearUnread = (key?: string) => {
  if (key === undefined) {
    if (unread.size > 0) setUnread(new Set())
  } else if (unread.has(key)) {
    const next = new Set(unread)
    next.delete(key)
    setUnread(next)
  }
}

export function useUnread(): ReadonlySet<string> {
  return useSyncExternalStore(
    (listener) => {
      unreadListeners.add(listener)
      return () => {
        unreadListeners.delete(listener)
      }
    },
    () => unread,
    () => unread
  )
}

/** The sentence for a layout changed on another screen (design 3.5, dash_conflict), said by writes. */
export const DASH_CONFLICT =
  'The dashboard was changed on another screen. Nothing was saved; it shows the latest now, so repeat the change.'

/**
 * Read one card with the `widget` op and put it in the dashboard held; null when the card could
 * not be placed. Reads of one card made at once (a 15-minute close ends several runs together)
 * are folded into one by the query cache. A card that is gone, or a layout that moved, is never
 * explained from the card's own answer (`not_found` can mean only the widget is gone, not the
 * screen): the dashboard is read again, and a read says nothing (design 3.5): nothing was
 * written, so no sentence about a change is true. A read that fails marks the card unread.
 */
export async function readCard(
  client: QueryClient,
  card: Pick<WidgetCard, 'screen_id' | 'kind'>
): Promise<WidgetCard | null> {
  try {
    const read = await client.fetchQuery({
      queryKey: widgetKey(card.screen_id, card.kind),
      queryFn: ({ signal }) => screenerApi.widget(card.screen_id, card.kind, signal),
      staleTime: 0,
      retry: false,
    })
    client.setQueryData<Dashboard>(DASHBOARD_KEY, (held) => (held ? putCard(held, read) : held))
    clearUnread(cardKey(card))
    return read
  } catch (error) {
    if (!(error instanceof ScreenerError) || (error.code !== 'gone' && error.code !== 'conflict')) {
      markUnread(cardKey(card))
      throw error
    }
    void client.invalidateQueries({ queryKey: DASHBOARD_KEY })
    return null
  }
}

/** A count card's strip takes the pushed run's point at once; the card's own read corrects it. */
function withPoint(counts: NonNullable<WidgetCard['counts']>, run: RunRow) {
  const last = counts.series.at(-1)
  if (last && last.at >= run.at) return counts
  return { ...counts, series: [...counts.series, { at: run.at, matches: run.matches }] }
}

/**
 * A pushed run changes what the push can say for certain, the run line and the counts, on every
 * card of its screen, and nothing else (design 3.7, LOW-B7): the push's `exited` names are
 * membership exits after the grace, not the run's rows going out, so patching rows from them moved
 * them twice. The rows move once, from the card's own read that follows. A run the card already
 * shows, or an older one that arrived late, changes nothing; null when no card is of this screen.
 */
export function patchRun(held: Dashboard, push: RunPush, run: RunRow): Dashboard | null {
  if (!held.widgets.some((card) => card.screen_id === push.screen_id)) return null
  return {
    ...held,
    last_run_id: Math.max(held.last_run_id, push.run_id),
    widgets: held.widgets.map((card) => {
      if (card.screen_id !== push.screen_id || (card.run && card.run.id >= push.run_id)) return card
      return {
        ...card,
        read_run: card.read_run !== undefined ? card.read_run : card.run,
        run,
        matches: push.counts.matches,
        not_checked: card.not_checked
          ? { ...card.not_checked, count: push.counts.not_checked }
          : card.not_checked,
        counts: card.counts && withPoint(card.counts, run),
      }
    }),
  }
}
