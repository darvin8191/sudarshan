/**
 * The Dashboard's cached read as plain functions (design 3.7): a card is (screen id, kind), never
 * an id, so a suggested layout at version 0 is addressed the same way as a stored one.
 */
import type { QueryClient } from '@tanstack/react-query'
import { screenerApi } from './api'
import { DASHBOARD_KEY, widgetKey } from './keys'
import type { Dashboard, WidgetCard } from './types'

export const cardKey = (card: Pick<WidgetCard, 'screen_id' | 'kind'>) =>
  `${card.screen_id}:${card.kind}`

/** The card read alone takes the place of the one held, in the same position. */
export function putCard(held: Dashboard, card: WidgetCard): Dashboard {
  return {
    ...held,
    widgets: held.widgets.map((each) => (cardKey(each) === cardKey(card) ? card : each)),
  }
}

/**
 * Read one card with the `widget` op and put it in the dashboard held. Reads of one card made at
 * once (a 15-minute close ends several runs together) are folded into one by the query cache.
 */
export async function readCard(
  client: QueryClient,
  card: Pick<WidgetCard, 'screen_id' | 'kind'>
): Promise<WidgetCard> {
  const read = await client.fetchQuery({
    queryKey: widgetKey(card.screen_id, card.kind),
    queryFn: ({ signal }) => screenerApi.widget(card.screen_id, card.kind, signal),
    staleTime: 0,
  })
  client.setQueryData<Dashboard>(DASHBOARD_KEY, (held) => (held ? putCard(held, read) : held))
  return read
}
