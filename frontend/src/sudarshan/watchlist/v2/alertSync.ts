import type { QueryClient } from '@tanstack/react-query'
import { type MarketAlertsState, marketAlertsApi } from '@/api/marketAlerts'
import { STATE_KEY as ALERTS_STATE_KEY } from '@/sudarshan/alerts/stateCache'
import type { Push } from './types'
import { refsOf } from './useListView'

/**
 * A list alert follows its list at once (M8.14, design 2.5, review M5). A pushed change names every
 * ref whose members it touched (`refs`); for each that carries a list alert, the page asks the alerts
 * to follow it now (`groups/sync`, the route the alerts' bridge already has), instead of leaving it to
 * the alerts' one-minute check. Which refs carry one is read from the Alerts state the page already
 * holds (`groups_by_ref`); nothing is read for it. A push of this page's own write is included: its
 * push is skipped for re-reading only.
 */

/**
 * The group refs a change to these refs touches: the ref itself when a list alert follows it, and,
 * for an own list, each of its sections that one follows (`list:7/section:2`), each once. A ref
 * whose groups are all stopped is left out (a stopped alert follows nothing); `lists` (a change the
 * server could not name) is left to the alerts' own check, as the server's push says.
 */
export function groupRefsFor(
  state: MarketAlertsState | undefined,
  refs: Iterable<string>
): string[] {
  const byRef = state && state.enabled !== false ? state.groups_by_ref : undefined
  if (!byRef) return []
  const stopped = new Set(
    (state?.groups ?? []).filter((group) => group.status === 'stopped').map((group) => group.id)
  )
  const follows = (ref: string) => (byRef[ref] ?? []).some((id) => !stopped.has(id))
  const out = new Set<string>()
  for (const ref of refs) {
    if (!ref || ref === 'lists') continue
    if (follows(ref)) out.add(ref)
    if (/^list:\d+$/.test(ref)) {
      for (const key of Object.keys(byRef))
        if (key.startsWith(`${ref}/section:`) && follows(key)) out.add(key)
    }
  }
  return [...out]
}

/**
 * Ask the alerts to follow every ref a batch of pushes names, once each. A refusal changes nothing
 * here: the alerts' one-minute check still follows the list, and the card says what it covers. It
 * never throws, so the push's own re-reads always run after it.
 */
export function syncListAlerts(client: QueryClient, pushes: readonly Push[]): void {
  try {
    const state = client.getQueryData<MarketAlertsState>(ALERTS_STATE_KEY)
    for (const ref of groupRefsFor(
      state,
      pushes.flatMap((push) => refsOf(push))
    )) {
      Promise.resolve()
        .then(() => marketAlertsApi.syncGroups(ref))
        .catch(() => {})
    }
  } catch {
    // A state the page cannot read: the alerts' own check follows the list.
  }
}
