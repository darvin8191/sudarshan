/**
 * The Alerts page's cached `/state` (M10.3, M10.7), changed by what the server pushes and by the
 * owner's own changes, as plain functions a test can call. Each returns a new state; none of them
 * reads the clock or the network.
 *
 * A push is applied where it is certain and read again where it is not (contract 2.3): an alert
 * the cache does not hold (set on another screen), a version newer than the cache's (an edit
 * elsewhere: its words may have changed too), and an engine line on its own (the market opened
 * or closed, the feed went down or came back: every card's check follows it).
 */
import type {
  AlertEvent,
  AlertRow,
  Channel,
  Delivery,
  EventsPage,
  MarketAlertPush,
  MarketAlertsState,
  MarketAlertsStatePush,
} from '@/api/marketAlerts'

export const STATE_KEY = ['sudarshan', 'market-alerts', 'state'] as const
/** Every cached History page: the History tab's, and an alert's own recent fires. */
export const EVENTS_KEY = ['sudarshan', 'market-alerts', 'events'] as const
/** The History tab's pages. */
export const HISTORY_KEY = [...EVENTS_KEY, 'list'] as const
/** One alert's latest fires, in its sheet. */
export const firesKey = (alertId: number) => [...EVENTS_KEY, 'alert', alertId] as const

export type Deliveries = Partial<Record<Channel, Delivery>>

/** An event with the settled deliveries given, each where it is newer than the one it holds. */
function settled(event: AlertEvent, news: Deliveries | undefined): AlertEvent {
  if (!news) return event
  let deliveries = event.deliveries
  for (const [channel, delivery] of Object.entries(news) as Array<[Channel, Delivery]>) {
    const held = deliveries[channel]
    if (held && held.updated_at > delivery.updated_at) continue
    if (held && held.updated_at === delivery.updated_at && held.status === delivery.status) continue
    deliveries = { ...deliveries, [channel]: delivery }
  }
  return deliveries === event.deliveries ? event : { ...event, deliveries }
}

function settledPage(page: EventsPage, news: ReadonlyMap<number, Deliveries>): EventsPage {
  let changed = false
  const events = page.events.map((event) => {
    const next = settled(event, news.get(event.id))
    if (next !== event) changed = true
    return next
  })
  return changed ? { ...page, events } : page
}

/**
 * Settled deliveries applied to a cached History page, or to the History tab's pages; an event
 * it does not hold is left alone. The same object back when nothing changed.
 */
export function applyDeliveries<T>(data: T, news: ReadonlyMap<number, Deliveries>): T {
  if (!data || typeof data !== 'object' || news.size === 0) return data
  if ('pages' in data && Array.isArray((data as { pages: unknown }).pages)) {
    const paged = data as T & { pages: EventsPage[] }
    const pages = paged.pages.map((page) => settledPage(page, news))
    return pages.every((page, index) => page === paged.pages[index]) ? data : { ...paged, pages }
  }
  if ('events' in data && Array.isArray((data as { events: unknown }).events)) {
    return settledPage(data as EventsPage, news) as T
  }
  return data
}

export function applyStatePush(
  state: MarketAlertsState,
  push: MarketAlertsStatePush
): { state: MarketAlertsState; reread: boolean } {
  const removed = new Set(push.removed ?? [])
  const changed = new Map((push.alerts ?? []).map((row) => [row.id, row]))
  let reread = changed.size === 0 && removed.size === 0
  const held = new Set(state.alerts.map((row) => row.id))
  for (const id of changed.keys()) if (!held.has(id)) reread = true

  const alerts: AlertRow[] = []
  for (const row of state.alerts) {
    if (removed.has(row.id)) continue
    const next = changed.get(row.id)
    if (!next || next.version < row.version) {
      alerts.push(row)
      continue
    }
    // A newer version keeps the cache's own until /state is read again, so an edit sent from
    // here meanwhile meets the server's conflict instead of overwriting the other screen's.
    if (next.version > row.version) reread = true
    alerts.push({
      ...row,
      status: next.status,
      status_reason: next.status_reason,
      check: next.check,
      fired_today: next.fired_today,
    })
  }
  return {
    state: { ...state, engine: push.engine ?? state.engine, alerts, server_time: push.server_time },
    reread,
  }
}

/** One more fire today on the alert a push names; `held` false when the cache lacks it. */
export function countFired(
  state: MarketAlertsState,
  push: Pick<MarketAlertPush, 'alert_id'>
): { state: MarketAlertsState; held: boolean } {
  let held = false
  const alerts = state.alerts.map((row) => {
    if (row.id !== push.alert_id) return row
    held = true
    return { ...row, fired_today: row.fired_today + 1 }
  })
  return { state: held ? { ...state, alerts } : state, held }
}

/** The server's answer to a create or a change, in place of what the cache held. */
export function putRow(state: MarketAlertsState, row: AlertRow): MarketAlertsState {
  const held = state.alerts.some((each) => each.id === row.id)
  const alerts = held
    ? state.alerts.map((each) => (each.id === row.id && each.version <= row.version ? row : each))
    : [...state.alerts, row]
  return { ...state, alerts }
}

/** An alert deleted, here or on another screen. */
export function dropRow(state: MarketAlertsState, id: number): MarketAlertsState {
  return { ...state, alerts: state.alerts.filter((row) => row.id !== id) }
}
