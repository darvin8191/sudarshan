/**
 * The Dashboard's cached read as plain functions (design 3.7): a card is (screen id, kind), never
 * an id, so a suggested layout at version 0 is addressed the same way as a stored one.
 */
import type { QueryClient } from '@tanstack/react-query'
import { toast } from '@/utils/toast'
import { ScreenerError, screenerApi } from './api'
import { DASHBOARD_KEY, widgetKey } from './keys'
import type { Dashboard, RunPush, RunRow, WidgetCard } from './types'

export const cardKey = (card: Pick<WidgetCard, 'screen_id' | 'kind'>) =>
  `${card.screen_id}:${card.kind}`

/** The card read alone takes the place of the one held, in the same position. */
export function putCard(held: Dashboard, card: WidgetCard): Dashboard {
  return {
    ...held,
    widgets: held.widgets.map((each) => (cardKey(each) === cardKey(card) ? card : each)),
  }
}

/** The sentence for a layout changed on another screen (design 3.5, dash_conflict). */
export const DASH_CONFLICT =
  'The dashboard was changed on another screen. Nothing was saved; it shows the latest now, so repeat the change.'

/**
 * Read one card with the `widget` op and put it in the dashboard held; null when the card could
 * not be placed. Reads of one card made at once (a 15-minute close ends several runs together)
 * are folded into one by the query cache. A card that is gone, or a layout that moved, is never
 * explained from the card's own answer (`not_found` can mean only the widget is gone, not the
 * screen): the dashboard is read again, and a layout conflict says so.
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
    return read
  } catch (error) {
    if (!(error instanceof ScreenerError) || (error.code !== 'gone' && error.code !== 'conflict'))
      throw error
    if (error.code === 'conflict') toast.info(DASH_CONFLICT)
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
