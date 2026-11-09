/**
 * The owner's changes to the dashboard (design 3.9) as plain functions and one hook. Every write
 * names the dashboard version it was made against; an answer of "the layout moved" reads the
 * dashboard again and says so, and nothing is retried behind the owner's back. A widget that is
 * gone while its screen is still there is a moved layout, never "that screen is no longer there".
 */
import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { toast } from '@/utils/toast'
import { problemOf, ScreenerError, screenerApi } from './api'
import { cardKey, DASH_CONFLICT } from './dashboardCache'
import { DASHBOARD_KEY, STATE_KEY } from './keys'
import type { Dashboard, DashboardWritten, ScreenerState, WidgetCard, WidgetKind } from './types'
import { useSend } from './useSend'

export const DASH_GONE = 'That screen is no longer there, so its widget was removed.'
export const DASH_REMOVED = 'Removed from the dashboard. The screen and its list are kept.'

/** Said under a switch that cannot be on: the screen is added Paused. */
export const pausedAddWords = (used: number, limit: number) =>
  `Added Paused: ${used} of ${limit} screens already check through the day. Pause one to start this.`

/**
 * Run one dashboard write. A conflict (or a missing widget whose screen the page still holds)
 * reads the dashboard again and throws the conflict sentence; a screen that is gone throws the
 * gone sentence (the server's own for an add, which names no widget); anything else passes through.
 * `screenId` is the screen the write is about, null for an add from a preset.
 */
export async function guarded<T>(
  client: QueryClient,
  screenId: number | null,
  run: () => Promise<T>
): Promise<T> {
  try {
    return await run()
  } catch (error) {
    const problem = problemOf(error)
    if (problem.code !== 'conflict' && problem.code !== 'gone') throw error
    void client.invalidateQueries({ queryKey: DASHBOARD_KEY })
    if (problem.code === 'gone') void client.invalidateQueries({ queryKey: STATE_KEY })
    const held = client
      .getQueryData<ScreenerState>(STATE_KEY)
      ?.screens.some((row) => row.id === screenId)
    if (problem.code === 'gone' && !held) {
      if (screenId === null) throw error
      throw new ScreenerError({ code: 'gone', message: DASH_GONE, field: null })
    }
    throw new ScreenerError({ code: 'conflict', message: DASH_CONFLICT, field: null })
  }
}

/** The dashboard held takes the version a write answered (the layout is stored from now on). */
export function noteWritten(
  client: QueryClient,
  written: DashboardWritten,
  change?: (held: Dashboard) => Dashboard
): void {
  client.setQueryData<Dashboard>(DASHBOARD_KEY, (held) =>
    held
      ? {
          ...(change ? change(held) : held),
          version: Math.max(held.version, written.version),
          suggested: false,
        }
      : held
  )
}

/** The whole order with one card moved one place; null at either end. */
export function movedKeys(
  cards: readonly WidgetCard[],
  card: Pick<WidgetCard, 'screen_id' | 'kind'>,
  by: -1 | 1
): Array<[number, WidgetKind]> | null {
  const keys = cards.map((each): [number, WidgetKind] => [each.screen_id, each.kind])
  const from = cards.findIndex((each) => cardKey(each) === cardKey(card))
  const to = from + by
  if (from < 0 || to < 0 || to >= keys.length) return null
  const [key] = keys.splice(from, 1)
  keys.splice(to, 0, key)
  return keys
}

/** Moving and removing: one write at a time for the whole layout, each sent once. */
export function useLayoutWrites(data: Dashboard | undefined) {
  const client = useQueryClient()
  const send = useSend()

  const tell = useCallback(
    (ok: boolean, done: string | null) => {
      if (ok) {
        if (done) toast.success(done)
      } else if (send.last.current) toast.error(send.last.current.message)
      return ok
    },
    [send.last]
  )

  const move = useCallback(
    async (card: WidgetCard, by: -1 | 1): Promise<boolean> => {
      if (!data) return false
      const keys = movedKeys(data.widgets, card, by)
      if (!keys) return false
      const ok = await send.run(async () => {
        const written = await guarded(client, card.screen_id, () =>
          screenerApi.widgetOrder({ version: data.version, keys })
        )
        noteWritten(client, written, (held) => ({
          ...held,
          widgets: keys.flatMap(([id, kind], position) => {
            const found = held.widgets.find((each) => each.screen_id === id && each.kind === kind)
            return found ? [{ ...found, position }] : []
          }),
        }))
      })
      return tell(ok, null)
    },
    [client, data, send, tell]
  )

  const remove = useCallback(
    async (card: WidgetCard): Promise<boolean> => {
      if (!data) return false
      const ok = await send.run(async () => {
        const written = await guarded(client, card.screen_id, () =>
          screenerApi.widgetRemove(card.screen_id, card.kind, data.version)
        )
        noteWritten(client, written, (held) => ({
          ...held,
          widgets: held.widgets
            .filter((each) => cardKey(each) !== cardKey(card))
            .map((each, position) => ({ ...each, position })),
        }))
      })
      return tell(ok, DASH_REMOVED)
    },
    [client, data, send, tell]
  )

  return { move, remove, pending: send.pending }
}
