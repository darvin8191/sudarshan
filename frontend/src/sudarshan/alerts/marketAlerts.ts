import type { QueryClient } from '@tanstack/react-query'
import type { Socket } from 'socket.io-client'
import {
  type AlertEvent,
  type EventsPage,
  type MarketAlertDeliveryPush,
  type MarketAlertPush,
  type MarketAlertsState,
  type MarketAlertsStatePush,
  marketAlertsApi,
} from '@/api/marketAlerts'
import { useAlertStore } from '@/stores/alertStore'
import { playChime, unlockSoundOnFirstTap } from '@/sudarshan/books/chime'
import { connectionCount } from '@/sudarshan/books/sharedSocket'
import { istTime } from '@/sudarshan/books/time'
import { istDay } from '@/sudarshan/dashboard/live'
import { toast } from '@/utils/toast'
import {
  applyDeliveries,
  applyStatePush,
  countFired,
  type Deliveries,
  EVENTS_KEY,
  firesKey,
  HISTORY_KEY,
  HISTORY_PAGE,
  type HistoryPages,
  mergeFirstPage,
  STATE_KEY,
  TEST_KEY,
} from './stateCache'

/**
 * Market alerts in the open app (M10.7): a toast and a sound of their own the moment the alert
 * service says one of the owner's market alerts fired, and on every connect the ones that fired
 * while the app was away. Telegram gets the same alerts from the server; nothing here sends
 * anything anywhere.
 *
 * What the server pushes, to the owner's room only (contract 2):
 * - `sudarshan_market_alert`: one fired alert, or a test: the toast and the sound;
 * - `sudarshan_market_alerts_state`: applied to the Alerts page's cached state (stateCache.ts);
 * - `sudarshan_market_alert_delivery`: a delivery that settled, applied to the cached History.
 *
 * After every connect, the first and each reconnect, it joins the room with an ack (a session
 * that is not the owner's is not joined, and hears nothing), then asks for what fired after the
 * last event this page has seen. A new page load starts from the newest event the server holds
 * and announces nothing from before it (contract 2.2: Telegram covers a closed app, History has
 * the rest); after that, every reconnect asks for what fired while the connection was down. One
 * missed alert gets the toast a pushed one gets; several get one toast, with View. A reconnect
 * also reads the page's state and an open History again: whatever was pushed meanwhile went to
 * nobody.
 *
 * Nothing missed is lost for good. From each connect until that connection's catch-up has read
 * what fired meanwhile, a push is shown but does not move the last id seen, so the catch-up
 * still asks from before the gap. A join without an ack and a read that failed are tried again
 * while the connection lasts, and failing that the next connect asks from the same place.
 *
 * Each event is announced once across the app's tabs, by the order alerts' rule
 * (books/orderAlerts.ts) on a channel of its own: the tab in use shows it at once and tells the
 * others, and a tab not in use waits a moment for that word. In a burst the first three show
 * each, and a fourth turns them into one toast for the whole burst, so none sits unseen behind
 * the toaster's three. The sound obeys the app's master sound switch, this device's "Market alert
 * sound" switch and the quiet window it shares with order sounds (chime.ts). A pushed alert may
 * always sound; one a catch-up tells sounds only when it fired in the last minute.
 */

/** Where the Alerts page lives (upstream owns /alerts, for the /trading chart's alerts). */
export const ALERTS_PATH = '/market-alerts'
/** A missed alert that fired longer ago than this makes no sound (a pushed one always may). */
export const SOUND_AGE_MS = 60_000
/** How long a join waits for its ack; without one, the next connect tries again. */
export const JOIN_WAIT_MS = 10_000
/** Pushes this close to the one before are one burst. */
export const BURST_MS = 300
/** How many alerts of a burst get a toast of their own. */
export const BURST_SHOWN = 3
export const TOAST_MS = 8_000
/** How long a tab not in use waits for the tab in use to show an alert. */
export const TAB_DEFER_MS = 400
/** A burst of fires reads History again once, this long after its last fire. */
export const HISTORY_SETTLE_MS = 500
const SINCE_LIMIT = 200
/** At most this many pages of missed alerts are read on one connect. */
const SINCE_PAGES = 25
/** How long a join without an ack, or a catch-up read that failed, waits before it is tried again. */
export const RETRY_MS = [2_000, 5_000, 10_000] as const

type Handler = (...args: any[]) => void
type On = (event: string, handler: Handler) => unknown
type Navigate = (to: string) => void

/** A bounded memory of keys, oldest forgotten first. */
class Keys {
  private readonly keys = new Set<string>()

  has(key: string): boolean {
    return this.keys.has(key)
  }

  /** True the first time a key is offered. */
  add(key: string): boolean {
    if (this.keys.has(key)) return false
    this.keys.add(key)
    if (this.keys.size > 1000) {
      const oldest = this.keys.values().next().value
      if (oldest !== undefined) this.keys.delete(oldest)
    }
    return true
  }

  clear(): void {
    this.keys.clear()
  }
}

/** Announced in one of the app's tabs. */
const shown = new Keys()
/** Counted into this tab's cached state. */
const counted = new Keys()

const eventKey = (id: number) => `e:${id}`

/** The newest event this page load has seen; null until its first catch-up starts it. */
let seen: number | null = null

function noteSeen(id: unknown): void {
  if (typeof id !== 'number' || !Number.isInteger(id) || id < 0) return
  if (seen === null || id > seen) seen = id
}

/**
 * Open from each connect until that connection's catch-up has read what fired meanwhile: a push
 * heard inside it is shown, and only its id is kept here, so the read still asks from before it.
 */
let gap: { connection: number; pushed: number } | null = null

let cache: QueryClient | null = null

/** The Alerts page's query client: what is pushed from now on keeps its cache current. */
export function bindMarketAlertsCache(client: QueryClient): void {
  cache = client
}

/** Deliveries that settled lately, for a History page whose read crossed their push. */
const recent = new Map<number, Deliveries>()

function rememberDelivery(push: MarketAlertDeliveryPush): void {
  const held = recent.get(push.event_id)
  recent.delete(push.event_id)
  recent.set(push.event_id, { ...held, [push.channel]: push.delivery })
  if (recent.size > 200) {
    const oldest = recent.keys().next().value
    if (oldest !== undefined) recent.delete(oldest)
  }
}

/** A History page just read, with any delivery that settled while the read was on its way. */
export function withRecentDeliveries<T>(data: T): T {
  return applyDeliveries(data, recent)
}

let go: Navigate = (to) => window.location.assign(to)

// ---------------------------------------------------------------------------
// One announcement across the app's tabs (books/orderAlerts.ts, on a channel of its own).

/** What the market alerts need of a BroadcastChannel. */
export interface TabChannel {
  postMessage(message: unknown): void
  onmessage: ((event: MessageEvent) => void) | null
  close(): void
}

let channel: TabChannel | null | undefined

/** This tab, among the app's tabs. */
const TAB_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`

/** The app's tab the trader used last, while it is on screen; null when none is. */
let inUse: string | null = null

const deferred = new Map<string, ReturnType<typeof setTimeout>>()

function forgetDeferred(key: string): void {
  const timer = deferred.get(key)
  if (timer !== undefined) clearTimeout(timer)
  deferred.delete(key)
}

function onTabMessage(event: MessageEvent): void {
  const data = (event.data ?? {}) as {
    key?: unknown
    ids?: unknown
    inUse?: unknown
    away?: unknown
  }
  if (typeof data.key === 'string') {
    shown.add(data.key)
    forgetDeferred(data.key)
  }
  if (Array.isArray(data.ids)) {
    for (const id of data.ids) {
      if (typeof id !== 'number') continue
      shown.add(eventKey(id))
      forgetDeferred(eventKey(id))
    }
  }
  if (typeof data.inUse === 'string') inUse = data.inUse
  if (typeof data.away === 'string' && inUse === data.away) inUse = null
}

function tabChannel(): TabChannel | null {
  if (channel !== undefined) return channel
  try {
    channel =
      typeof BroadcastChannel === 'function'
        ? new BroadcastChannel('sudarshan-market-alerts')
        : null
  } catch {
    channel = null
  }
  if (channel) {
    channel.onmessage = onTabMessage
    // Node's channel (the test runner's) would keep its process alive; browsers have no unref.
    ;(channel as { unref?: () => void }).unref?.()
  }
  return channel
}

function tellOtherTabs(message: unknown): void {
  try {
    tabChannel()?.postMessage(message)
  } catch {
    // A closed channel: the other tabs may show it too, nothing worse.
  }
}

/** Tests only: stand in for the browser's channel between tabs. */
export function setTabChannelForTests(fake: TabChannel | null): void {
  channel = fake
  if (fake) fake.onmessage = onTabMessage
}

function focused(): boolean {
  return typeof document.hasFocus === 'function' ? document.hasFocus() : true
}

function nowInUse(): void {
  inUse = TAB_ID
  tellOtherTabs({ inUse: TAB_ID })
}

function nowAway(): void {
  if (inUse === TAB_ID) inUse = null
  tellOtherTabs({ away: TAB_ID })
}

let watchingUse = false

function watchUse(): void {
  if (watchingUse || typeof window === 'undefined' || typeof document === 'undefined') return
  watchingUse = true
  window.addEventListener('focus', nowInUse)
  window.addEventListener('pagehide', nowAway)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') nowAway()
    else if (focused()) nowInUse()
  })
  if (document.visibilityState !== 'hidden' && focused()) nowInUse()
}

function showsAtOnce(): boolean {
  if (typeof document === 'undefined') return true
  if (document.visibilityState === 'hidden') return false
  if (focused()) return true
  return inUse === null || inUse === TAB_ID
}

// ---------------------------------------------------------------------------

interface Announcement {
  key: string
  /** The events it tells of. */
  ids: number[]
  title: string
  description: string
  /** The server's title of the newest event it tells of. */
  headline: string
  /** Where View goes. */
  view: string
  /** When the newest event it tells of fired. */
  at: number
  /** Pushed as it fired, so part of a burst. */
  live: boolean
}

const DAY_MONTH = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})

/** "10:42:05", with its day before it when that is not today in India: "21 Oct 10:42:05". */
export function firedAt(at: number, now = Date.now()): string {
  return istDay(at) === istDay(now) ? istTime(at) : `${DAY_MONTH.format(at)} ${istTime(at)}`
}

const when = firedAt

function described(at: number, detail: string, note: string): string {
  return [when(at), detail, note ? `Note: ${note}` : ''].filter(Boolean).join(' · ')
}

function viewOf(alertId: number): string {
  return alertId > 0 ? `${ALERTS_PATH}?alert=${alertId}` : `${ALERTS_PATH}?tab=history`
}

function single(event: {
  /** Null for a fire the service could not record: shown, but never kept as an id. */
  id: number | null
  alertId: number
  title: string
  detail: string
  note: string
  at: number
  live: boolean
}): Announcement {
  return {
    // An unrecorded fire has no id: its key is its own words and moment, and only this page load
    // and the other tabs ever see it.
    key: event.id === null ? `n:${event.at}:${event.alertId}:${event.title}` : eventKey(event.id),
    ids: event.id === null ? [] : [event.id],
    title: `Alert · ${event.title}`,
    description: described(event.at, event.detail, event.note),
    headline: event.title,
    view: viewOf(event.alertId),
    at: event.at,
    live: event.live,
  }
}

/** Missed alerts, oldest first: one gets its own toast, several share one. */
function missedOf(events: AlertEvent[]): Announcement | null {
  const newest = events.at(-1)
  if (!newest) return null
  if (events.length === 1) {
    return single({
      id: newest.id,
      alertId: newest.alert_id,
      title: newest.title,
      detail: newest.detail ?? '',
      note: newest.note ?? '',
      at: newest.at,
      live: false,
    })
  }
  return {
    key: `m:${newest.id}`,
    ids: events.map((event) => event.id),
    title: `${events.length} alerts fired while the app was away`,
    description: `Latest ${when(newest.at)} · ${newest.title}`,
    headline: newest.title,
    view: `${ALERTS_PATH}?tab=history`,
    at: newest.at,
    live: false,
  }
}

const VIEW_BUTTON = { actionButton: 'min-h-12 min-w-12' }

function show(id: string, title: string, description: string, view: string): void {
  toast.info(title, {
    id,
    description,
    duration: TOAST_MS,
    classNames: VIEW_BUTTON,
    action: { label: 'View', onClick: () => go(view) },
  })
}

interface Burst {
  /** When its newest alert came: the next within `BURST_MS` of it joins (the window slides). */
  last: number
  count: number
  /** Its alerts' own toasts, while they are still up. */
  singles: string[]
  /** Its one toast, once it has more than three. */
  id: string
}

function newBurst(now: number): Burst {
  return { last: now, count: 0, singles: [], id: `burst:${now}` }
}

let burst = newBurst(Number.NEGATIVE_INFINITY)

function showLive(alert: Announcement, now: number): void {
  if (now - burst.last > BURST_MS) burst = newBurst(now)
  burst.last = now
  burst.count += 1
  if (burst.count <= BURST_SHOWN) {
    burst.singles.push(alert.key)
    show(alert.key, alert.title, alert.description, alert.view)
    return
  }
  // More than three: one toast for the whole burst in place of the three (design 5.2). The
  // toaster draws three, so a fourth single would hide the first; History has every one.
  for (const key of burst.singles) toast.dismiss(key)
  burst.singles = []
  const description = `Latest ${when(alert.at, now)} · ${alert.headline}`
  show(burst.id, `${burst.count} alerts fired`, description, `${ALERTS_PATH}?tab=history`)
}

/** Show it in this tab now, tell the other tabs, and sound it if it is fresh. */
function announce(alert: Announcement): void {
  if (!shown.add(alert.key)) return
  forgetDeferred(alert.key)
  for (const id of alert.ids) shown.add(eventKey(id))
  tellOtherTabs({ key: alert.key, ids: alert.ids })
  const settings = useAlertStore.getState()
  if (!settings.shouldShowToast()) return
  const now = Date.now()
  if (alert.live) showLive(alert, now)
  else show(alert.key, alert.title, alert.description, alert.view)
  // A pushed alert is news now, whatever the phone's clock says of its time; a missed one is old
  // news after a minute.
  const fresh = alert.live || now - alert.at <= SOUND_AGE_MS
  if (fresh && settings.shouldPlaySound()) playChime('market', now)
}

function deliver(alert: Announcement): void {
  if (shown.has(alert.key) || deferred.has(alert.key)) return
  tabChannel()
  if (showsAtOnce()) {
    announce(alert)
    return
  }
  deferred.set(
    alert.key,
    setTimeout(() => {
      deferred.delete(alert.key)
      announce(alert)
    }, TAB_DEFER_MS)
  )
}

// ---------------------------------------------------------------------------
// What the server pushes.

function onAlert(push: MarketAlertPush): void {
  if (!push || (push.event_id !== null && typeof push.event_id !== 'number')) return
  const id = push.event_id
  const at = typeof push.at_ms === 'number' ? push.at_ms : Date.now()
  if (id !== null) {
    if (gap) gap.pushed = Math.max(gap.pushed, id)
    else noteSeen(id)
  }
  // Counted once per event; an unrecorded fire has no id to count by, and a repeat of the same
  // push (same words, same moment) is not counted twice.
  const countKey =
    id === null ? `n:${at}:${push.alert_id}:${String(push.title ?? '')}` : eventKey(id)
  if (counted.add(countKey)) {
    if (!push.test) countInCache(push)
    refreshLater(push.alert_id)
  }
  deliver(
    single({
      id,
      alertId: push.alert_id,
      title: String(push.title ?? ''),
      detail: String(push.detail ?? ''),
      note: String(push.note ?? ''),
      at,
      live: true,
    })
  )
}

// ---------------------------------------------------------------------------
// History read again after fires: once per burst, the newest page only, never cutting short a
// Show older on its way.

const refresh: { timer: ReturnType<typeof setTimeout> | null; alerts: Set<number> } = {
  timer: null,
  alerts: new Set(),
}

/** Read History's newest page and the fired alerts' last fires once the burst has settled. */
function refreshLater(alertId: number): void {
  if (!cache) return
  if (alertId > 0) refresh.alerts.add(alertId)
  if (refresh.timer) clearTimeout(refresh.timer)
  refresh.timer = setTimeout(() => {
    refresh.timer = null
    const alerts = [...refresh.alerts]
    refresh.alerts.clear()
    void refreshHistory()
    for (const id of alerts) {
      void cache?.invalidateQueries({ queryKey: firesKey(id) }, { cancelRefetch: false })
    }
  }, HISTORY_SETTLE_MS)
}

/** History's newest page read again in place of the one held; the older pages stay as they are. */
async function refreshHistory(): Promise<void> {
  const client = cache
  const query = client?.getQueryCache().find({ queryKey: HISTORY_KEY, exact: true })
  if (!client || !query?.state.data) return
  // Not on screen: it is read when it is next opened.
  if (query.getObserversCount() === 0) {
    void client.invalidateQueries({ queryKey: HISTORY_KEY, exact: true, refetchType: 'none' })
    return
  }
  // Show older on its way: never cut short; read again once it settles.
  if (query.state.fetchStatus === 'fetching') {
    refreshLater(0)
    return
  }
  try {
    const first = withRecentDeliveries(await marketAlertsApi.events({ limit: HISTORY_PAGE }))
    let whole = false
    client.setQueryData<HistoryPages>(HISTORY_KEY, (held) => {
      if (!held) return held
      const merged = mergeFirstPage(held, first)
      whole = merged === null
      return merged ?? held
    })
    // More fired than one page holds: the pages in between are read with the rest.
    if (whole)
      void client.refetchQueries({ queryKey: HISTORY_KEY, exact: true }, { cancelRefetch: false })
  } catch {
    // Not read: the whole list is read again, and a failure there shows on the list.
    void client.refetchQueries({ queryKey: HISTORY_KEY, exact: true }, { cancelRefetch: false })
  }
}

/**
 * The last test from the settings, read again while a delivery is still sending: its event, the
 * one after the id before it (events/since), with how each channel delivered it.
 */
function rereadTest(): void {
  const client = cache
  const held = client?.getQueryData<EventsPage>(TEST_KEY)?.events[0]
  if (!client || !held) return
  const sending = Object.values(held.deliveries ?? {}).some((each) => each?.status === 'pending')
  if (!sending) return
  void marketAlertsApi
    .eventsSince(held.id - 1, 1)
    .then((page) => {
      const fresh = page.events.find((each) => each.id === held.id)
      if (!fresh) return
      client.setQueryData<EventsPage>(TEST_KEY, (now) =>
        now?.events[0]?.id === held.id
          ? withRecentDeliveries({ events: [fresh], more: false })
          : now
      )
    })
    .catch(() => {
      // Not read: it stays as it is until the next push or reconnect.
    })
}

function countInCache(push: MarketAlertPush): void {
  const state = cache?.getQueryData<MarketAlertsState>(STATE_KEY)
  if (!cache || !state) return
  const result = countFired(state, push)
  if (result.held) cache.setQueryData(STATE_KEY, result.state)
  // Not held (set on another screen), or a /state read on its way whose answer may predate this
  // fire and would take the count away: read again.
  if (!result.held || cache.isFetching({ queryKey: STATE_KEY }) > 0) {
    void cache.invalidateQueries({ queryKey: STATE_KEY })
  }
}

function onState(push: MarketAlertsStatePush): void {
  if (!cache || !push || typeof push !== 'object') return
  const state = cache.getQueryData<MarketAlertsState>(STATE_KEY)
  let reread =
    push.resync === true || state === undefined || cache.isFetching({ queryKey: STATE_KEY }) > 0
  if (state) {
    const applied = applyStatePush(state, push)
    cache.setQueryData(STATE_KEY, applied.state)
    reread ||= applied.reread
  }
  if (reread) void cache.invalidateQueries({ queryKey: STATE_KEY })
}

function onDelivery(push: MarketAlertDeliveryPush): void {
  if (!push || typeof push.event_id !== 'number' || !push.delivery) return
  if (push.channel !== 'telegram' && push.channel !== 'app') return
  rememberDelivery(push)
  const news = new Map([[push.event_id, { [push.channel]: push.delivery } as Deliveries]])
  cache?.setQueriesData({ queryKey: EVENTS_KEY }, (data: unknown) => applyDeliveries(data, news))
  // The test's other channel is not pushed (contract 2.4): read it if it is still sending.
  if (cache?.getQueryData<EventsPage>(TEST_KEY)?.events[0]?.id === push.event_id) rereadTest()
}

// ---------------------------------------------------------------------------
// Joining the owner's room, and what fired while the app was away.

/** The connection on which each socket last joined (its connect count). */
const joined = new WeakMap<Socket, number>()

let listening: { socket: Socket; onConnect: Handler } | null = null

/** Whether this screen hears the owner's room: unknown before the app has a connection at all. */
export type Hearing = 'unknown' | 'joined' | 'not_joined'

let hearing: Hearing = 'unknown'
const hearingListeners = new Set<() => void>()

function hear(next: Hearing): void {
  if (hearing === next) return
  hearing = next
  for (const listener of [...hearingListeners]) listener()
}

/** Whether this screen hears the owner's room now (M10.7). */
export function hearingNow(): Hearing {
  return hearing
}

/** Be told when that changes; the answer stops it. */
export function onHearing(listener: () => void): () => void {
  hearingListeners.add(listener)
  return () => {
    hearingListeners.delete(listener)
  }
}

/** Retries waiting, so a reset (tests) or a newer connection can forget them. */
const retries = new Set<ReturnType<typeof setTimeout>>()

/** Run `again` after the `attempt`th wait, unless every wait is spent. */
function retryLater(attempt: number, again: () => void): void {
  const wait = RETRY_MS[attempt]
  if (wait === undefined) return
  const timer = setTimeout(() => {
    retries.delete(timer)
    again()
  }, wait)
  retries.add(timer)
}

/** Still connected, and this page's handlers still on the socket (not signed out, not left). */
function stillListening(socket: Socket): boolean {
  const own = listening
  if (!own || own.socket !== socket || !socket.connected) return false
  return socket.listeners('connect').includes(own.onConnect)
}

/** The connection a socket is on now, by its connect count. */
const connectionOf = (socket: Socket) => connectionCount(socket) ?? 1

/** Still on the connection that asked: a newer one joins and catches up on its own. */
const sameConnection = (socket: Socket, connection: number) =>
  stillListening(socket) && connectionOf(socket) === connection

function join(socket: Socket, connection: number, attempt = 0): void {
  socket
    .timeout(JOIN_WAIT_MS)
    .emit('sudarshan_alerts_join', (error: unknown, ack?: { ok?: unknown }) => {
      if (!sameConnection(socket, connection)) return
      if (error) {
        // No ack in time: the room may or may not be joined. Ask again while this connection lasts.
        retryLater(attempt, () => {
          if (sameConnection(socket, connection) && joined.get(socket) !== connection) {
            join(socket, connection, attempt + 1)
          }
        })
        return
      }
      if (ack?.ok !== true) return
      joined.set(socket, connection)
      hear('joined')
      // The page's state is read after every join (contract 2.1): a state push lost while no app
      // was connected would leave a card showing the wrong status.
      if (cache) void cache.invalidateQueries({ queryKey: STATE_KEY })
      if (connection > 1 && cache) {
        // A History page on its way (Show older) is not cut short: it is read again after.
        void cache.invalidateQueries({ queryKey: EVENTS_KEY }, { cancelRefetch: false })
        rereadTest()
      }
      void catchUp(socket, connection)
    })
}

/** Not a test, and not an alert sent with the app turned off. */
function worthTelling(event: AlertEvent): boolean {
  return !event.test && event.deliveries?.app?.status !== 'off'
}

/** The connection's gap is read: what was pushed inside it counts as seen, and it closes. */
function closeGap(connection: number): void {
  if (gap?.connection !== connection) return
  noteSeen(gap.pushed || null)
  gap = null
}

/**
 * Read what fired since the last event seen, and tell it. A new page load reads /state instead
 * and starts from the newest event there, telling nothing. Throws when a read fails.
 */
async function catchUpOnce(socket: Socket, connection: number): Promise<void> {
  if (seen === null) {
    const state = await marketAlertsApi.state()
    if (!stillListening(socket)) return
    const running = state.engine.code !== 'starting' && state.engine.code !== 'off'
    if (!state.enabled || state.engine.code === 'off') return
    // Starting up: its newest event is not settled yet; ask again in a moment.
    if (!running) throw new Error('starting')
    noteSeen(state.last_event_id)
    if (connectionOf(socket) === connection) closeGap(connection)
    return
  }
  let after = seen
  const missed: AlertEvent[] = []
  for (let page = 0; page < SINCE_PAGES; page += 1) {
    const { events, more } = await marketAlertsApi.eventsSince(after, SINCE_LIMIT)
    for (const event of events) {
      if (event.id <= after) continue
      after = event.id
      if (worthTelling(event)) missed.push(event)
    }
    if (!more || events.length === 0) break
  }
  // Dropped meanwhile, or signed out: nothing told, so nothing noted; the next connect asks again.
  if (!stillListening(socket)) return
  noteSeen(after)
  // Only this connection's own read closes its gap: an older one read before the gap began.
  if (connectionOf(socket) === connection) closeGap(connection)
  // Shown here or in another tab meanwhile, or about to be: told once.
  const fresh = missed.filter(
    (event) => !shown.has(eventKey(event.id)) && !deferred.has(eventKey(event.id))
  )
  const told = missedOf(fresh)
  if (told) deliver(told)
}

let catching: Promise<void> | null = null
/** The connection a catch-up is asked for next. */
let next: { socket: Socket; connection: number; attempt: number } | null = null

/**
 * One catch-up at a time; a connect during one runs it once more after, on that connection. A
 * read that fails is tried again while its connection lasts.
 */
function catchUp(socket: Socket, connection: number, attempt = 0): Promise<void> {
  next = { socket, connection, attempt }
  if (catching) return catching
  catching = (async () => {
    while (next) {
      const current = next
      next = null
      try {
        await catchUpOnce(current.socket, current.connection)
      } catch {
        // Not read (the service is starting or not answering): again in a while, on this
        // connection; failing that, the next connect asks from the same place.
        retryLater(current.attempt, () => {
          if (sameConnection(current.socket, current.connection)) {
            void catchUp(current.socket, current.connection, current.attempt + 1)
          }
        })
      }
    }
  })().finally(() => {
    catching = null
  })
  return catching
}

/**
 * Listen on the app's shared connection, registering through `on`, which the caller records
 * so its own cleanup removes these handlers with its others (hooks/useSocket.ts). `navigate`
 * is how a toast's View opens the Alerts page without reloading the app.
 */
export function installMarketAlerts(socket: Socket, on: On, navigate?: Navigate): void {
  if (navigate) go = navigate
  unlockSoundOnFirstTap()
  tabChannel()
  watchUse()
  const onConnect = () => {
    const connection = connectionOf(socket)
    // What is pushed from here until this connection's catch-up has read the gap stays above it.
    gap = { connection, pushed: gap?.pushed ?? 0 }
    hear('not_joined')
    join(socket, connection)
  }
  on('connect', onConnect)
  on('disconnect', () => hear('not_joined'))
  on('sudarshan_market_alert', onAlert)
  on('sudarshan_market_alerts_state', onState)
  on('sudarshan_market_alert_delivery', onDelivery)
  listening = { socket, onConnect }
  // Installed again on a connection already up (a layout swap): joined already, unless not.
  const connection = connectionOf(socket)
  if (!socket.connected) hear('not_joined')
  else if (joined.get(socket) === connection) hear('joined')
  else onConnect()
}

/** Tests only: forget every alert shown, counted and seen, and the bound cache. */
export function resetMarketAlertsForTests(): void {
  shown.clear()
  counted.clear()
  recent.clear()
  for (const timer of deferred.values()) clearTimeout(timer)
  deferred.clear()
  inUse = null
  cache = null
  listening = null
  catching = null
  next = null
  seen = null
  gap = null
  hearing = 'unknown'
  for (const timer of retries) clearTimeout(timer)
  retries.clear()
  if (refresh.timer) clearTimeout(refresh.timer)
  refresh.timer = null
  refresh.alerts.clear()
  burst = newBurst(Number.NEGATIVE_INFINITY)
  go = (to) => window.location.assign(to)
}
