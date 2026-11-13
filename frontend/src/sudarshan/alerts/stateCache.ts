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
  GroupBrief,
  GroupRow,
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
/** The last test from the settings, kept as a page of one so its deliveries settle by push too. */
export const TEST_KEY = [...EVENTS_KEY, 'test'] as const
/** Where alerts go by default (contract 1.7). */
export const PREFS_KEY = ['sudarshan', 'market-alerts', 'prefs'] as const

/** How many events a History page holds. */
export const HISTORY_PAGE = 50

export type Deliveries = Partial<Record<Channel, Delivery>>

/** The History tab's pages as React Query holds them. */
export interface HistoryPages {
  pages: EventsPage[]
  pageParams: unknown[]
}

/**
 * The History tab's pages with its newest page read again in place of what the first page held,
 * the older pages left as they are; null when more fired since than one page holds, so the pages
 * in between are not known and the whole list must be read again.
 */
export function mergeFirstPage(held: HistoryPages, first: EventsPage): HistoryPages | null {
  const head = held.pages[0]
  if (!head) return null
  const newestHeld = head.events[0]?.id ?? 0
  const oldestFresh = first.events.at(-1)?.id ?? Number.POSITIVE_INFINITY
  if (first.more && oldestFresh > newestHeld) return null
  const fresh = new Set(first.events.map((event) => event.id))
  const pages = held.pages.map((page, index) => {
    const kept = page.events.filter((event) => !fresh.has(event.id))
    if (index === 0) return { ...page, events: [...first.events, ...kept] }
    return kept.length === page.events.length ? page : { ...page, events: kept }
  })
  return { ...held, pages }
}

const same = (a: Delivery, b: Delivery) =>
  a.status === b.status &&
  a.reason === b.reason &&
  a.attempts === b.attempts &&
  a.updated_at === b.updated_at

/**
 * An event with the settled deliveries given in place of the ones it holds (contract 2.4), except
 * where it holds a later one: a read that crossed the push already has what settled.
 */
function settled(event: AlertEvent, news: Deliveries | undefined): AlertEvent {
  if (!news) return event
  let deliveries = event.deliveries
  for (const [channel, delivery] of Object.entries(news) as Array<[Channel, Delivery]>) {
    const held = deliveries[channel]
    if (held && (held.updated_at > delivery.updated_at || same(held, delivery))) continue
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
    return settledPage(data as unknown as EventsPage, news) as T
  }
  return data
}

export function applyStatePush(
  state: MarketAlertsState,
  push: MarketAlertsStatePush
): { state: MarketAlertsState; reread: boolean } {
  // Sent before the read the cache holds (and delivered after it): the read already says more.
  if (typeof push.server_time === 'number' && push.server_time < state.server_time) {
    return { state, reread: false }
  }
  const removed = new Set(push.removed ?? [])
  const changed = new Map((push.alerts ?? []).map((row) => [row.id, row]))
  const groupsNamed = (push.groups?.length ?? 0) + (push.removed_groups?.length ?? 0) > 0
  let reread = changed.size === 0 && removed.size === 0 && !groupsNamed
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
  const applied = applyGroupPush(state.groups, push)
  if (applied.reread) reread = true
  return {
    state: {
      ...state,
      engine: push.engine ?? state.engine,
      alerts,
      ...(applied.groups ? { groups: applied.groups } : {}),
      server_time: push.server_time,
    },
    reread,
  }
}

/**
 * The group cards after a push (M8.14): a card the push names as removed goes; a changed card
 * takes the push's status, words and coverage. A group the cache does not hold, and a newer
 * version than the cache's (an edit elsewhere changed its condition and words too), are read
 * again; the cache keeps its own copy until then. A card's "could not read the list" words clear
 * only when the push shows a later sync. `groups` is undefined when nothing changed.
 */
function applyGroupPush(
  held: GroupRow[] | undefined,
  push: Pick<MarketAlertsStatePush, 'groups' | 'removed_groups'>
): { groups: GroupRow[] | undefined; reread: boolean } {
  const changed = new Map((push.groups ?? []).map((brief) => [brief.id, brief]))
  const removed = new Set(push.removed_groups ?? [])
  if (changed.size === 0 && removed.size === 0) return { groups: undefined, reread: false }
  const cards = held ?? []
  let reread = false
  for (const id of changed.keys()) if (!cards.some((card) => card.id === id)) reread = true
  const groups = cards
    .filter((card) => !removed.has(card.id))
    .map((card) => {
      const brief = changed.get(card.id)
      if (!brief || brief.version < card.version) return card
      if (brief.version > card.version) reread = true
      return briefed(card, brief)
    })
  return { groups, reread }
}

function briefed(card: GroupRow, brief: GroupBrief): GroupRow {
  const synced = (brief.synced_at ?? 0) > (card.synced_at ?? 0)
  return {
    ...card,
    status: brief.status,
    status_reason: brief.status_reason,
    covered: brief.covered,
    left_out: brief.left_out,
    synced_at: brief.synced_at,
    members: synced ? brief.covered : card.members,
    follow: synced ? '' : card.follow,
  }
}

/**
 * One more fire today on the alert a push names, when the cache holds it as Active; `held` false
 * when the cache lacks it. The state push's count wins (contract 2.2): the server sends the state
 * push for a fire that stops a Once alert before that fire's alert push, so the row it names is
 * no longer Active and is not counted twice.
 */
export function countFired(
  state: MarketAlertsState,
  push: Pick<MarketAlertPush, 'alert_id'>
): { state: MarketAlertsState; held: boolean } {
  let held = false
  let counted = false
  const alerts = state.alerts.map((row) => {
    if (row.id !== push.alert_id) return row
    held = true
    if (row.status !== 'active') return row
    counted = true
    return { ...row, fired_today: row.fired_today + 1 }
  })
  return { state: counted ? { ...state, alerts } : state, held }
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

/** The server's answer to a make or a change of a list alert, in place of what the cache held. */
export function putGroup(state: MarketAlertsState, group: GroupRow): MarketAlertsState {
  const held = state.groups ?? []
  const has = held.some((each) => each.id === group.id)
  const groups = has
    ? held.map((each) => (each.id === group.id && each.version <= group.version ? group : each))
    : [...held, group]
  const refs = { ...(state.groups_by_ref ?? {}) }
  refs[group.ref] = [...new Set([...(refs[group.ref] ?? []), group.id])]
  return { ...state, groups, groups_by_ref: refs }
}

/** A list alert deleted, here or on another screen, with its member alerts. */
export function dropGroup(state: MarketAlertsState, id: number): MarketAlertsState {
  const refs: Record<string, number[]> = {}
  for (const [ref, ids] of Object.entries(state.groups_by_ref ?? {})) {
    const kept = ids.filter((each) => each !== id)
    if (kept.length > 0) refs[ref] = kept
  }
  return {
    ...state,
    groups: (state.groups ?? []).filter((each) => each.id !== id),
    groups_by_ref: refs,
    alerts: state.alerts.filter((row) => row.group_id !== id),
  }
}
