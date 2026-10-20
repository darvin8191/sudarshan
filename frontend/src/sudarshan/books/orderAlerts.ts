import type { Socket } from 'socket.io-client'
import { type AlertCategories, useAlertStore } from '@/stores/alertStore'
import { formatAmount, formatRupees } from '@/sudarshan/dashboard/format'
import { showToast } from '@/utils/toast'
import { type ChimeKind, playChime, unlockSoundOnFirstTap } from './chime'
import { cancelAllTargets, notePushedFrame } from './orderFold'
import { istTime } from './time'

/**
 * App-wide order alerts (M6.8, in-app part): a toast, and a soft sound, the
 * moment the broker reports that an order filled, partly filled, was rejected
 * or was cancelled at the broker. The server pushes these as Socket.IO
 * `order_update` frames (subscribers/socketio_subscriber.py), from the Upstox
 * order stream in Live mode and from the sandbox engine in Analyze mode.
 *
 * Placing, modifying and cancelling from this app already raise their own
 * toasts, so a frame that only says an order is open or waiting for its
 * trigger raises none, and a cancel this app asked for is not announced again
 * as one the broker made. Each order id, status and filled quantity is
 * announced once, however often the frame arrives, the shell remounts or
 * the app is open in more than one tab, and each alert carries the time it
 * came, in India time, so one seen late does not read as new.
 *
 * Telegram gets the same events from a server-side service; nothing here
 * sends anything anywhere.
 */

/** The `order_update` payload, as socketio_subscriber.py emits it. */
export interface OrderUpdateFrame {
  mode?: string
  broker?: string
  orderid?: string | number
  symbol?: string
  exchange?: string
  action?: string
  quantity?: unknown
  price?: unknown
  trigger_price?: unknown
  pricetype?: string
  product?: string
  order_status?: string
  filled_quantity?: unknown
  pending_quantity?: unknown
  average_price?: unknown
  rejection_reason?: string
}

export type OrderAlertKind = ChimeKind

export interface OrderAlert {
  /** Order id, status and filled quantity: what makes an alert the same alert. */
  key: string
  orderid: string
  kind: OrderAlertKind
  /** Analyze mode: the sandbox, not the broker, moved the order. */
  sandbox: boolean
  title: string
  description: string
}

const DONE = new Set(['complete', 'completed', 'filled', 'executed', 'traded'])
const REFUSED = new Set(['rejected', 'failed'])
const OFF = new Set(['cancelled', 'canceled', 'expired', 'lapsed'])

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

function amount(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(text(value).replace(/,/g, ''))
  return Number.isFinite(n) ? Math.abs(n) : 0
}

function units(n: number): string {
  return Number.isInteger(n) ? formatAmount(n, 0) : String(n)
}

/**
 * What a frame says, in a trader's words, or null when it says nothing worth
 * an alert (an order placed, modified, or still waiting).
 */
export function describeOrderUpdate(frame: OrderUpdateFrame): OrderAlert | null {
  const orderid = text(frame.orderid)
  if (!orderid) return null
  const status = text(frame.order_status).toLowerCase()
  const quantity = amount(frame.quantity)
  const filled = amount(frame.filled_quantity)
  const average = amount(frame.average_price)
  const sandbox = frame.mode === 'analyze' || frame.mode === 'analyzer'
  const side = text(frame.action).toUpperCase()
  const symbol = text(frame.symbol)
  const order = (withQuantity: boolean) =>
    [side, withQuantity && quantity > 0 ? units(quantity) : '', symbol || `order ${orderid}`]
      .filter(Boolean)
      .join(' ')
  const at = average > 0 ? ` at ${formatRupees(average)}` : ''

  let kind: OrderAlertKind
  let title: string
  let description: string
  if (DONE.has(status)) {
    kind = 'filled'
    title = `Filled · ${order(true)}`
    const done = filled > 0 ? filled : quantity
    description = done > 0 ? `${units(done)} filled${at}${at ? ' average' : ''}` : 'Filled in full'
  } else if (REFUSED.has(status)) {
    kind = 'rejected'
    title = `Rejected · ${order(true)}`
    const reason = text(frame.rejection_reason)
    const who = sandbox ? 'Sandbox' : 'Broker'
    description = reason ? `${who} says: ${reason}` : `The ${who.toLowerCase()} gave no reason.`
  } else if (OFF.has(status)) {
    kind = 'cancelled'
    title = `Cancelled · ${order(true)}`
    description = sandbox
      ? 'Cancelled by the sandbox, not from this app.'
      : 'Cancelled at the broker, not from this app.'
    if (filled > 0) description += ` ${units(filled)} of ${units(quantity)} had filled.`
  } else if (filled > 0 && (quantity === 0 || filled < quantity)) {
    kind = 'partial'
    title = `Partly filled · ${order(false)}`
    description =
      quantity > 0
        ? `${units(filled)} of ${units(quantity)} filled${at}`
        : `${units(filled)} filled${at}`
    if (at) description += ' average'
  } else {
    return null
  }
  if (sandbox) title = `Sandbox · ${title}`
  return { key: `${orderid}|${status}|${filled}`, orderid, kind, sandbox, title, description }
}

/** A bounded memory of the alerts already shown, oldest forgotten first. */
class Shown {
  private readonly keys = new Set<string>()
  private readonly limit = 500

  has(key: string): boolean {
    return this.keys.has(key)
  }

  /** True the first time a key is offered. */
  add(key: string): boolean {
    if (this.keys.has(key)) return false
    this.keys.add(key)
    if (this.keys.size > this.limit) {
      const oldest = this.keys.values().next().value
      if (oldest !== undefined) this.keys.delete(oldest)
    }
    return true
  }

  clear(): void {
    this.keys.clear()
  }
}

/** Shared across remounts of the shell, so a remount never announces a fill again. */
const shown = new Shown()

/** How long a cancel this app asked for keeps the broker's cancel quiet. */
export const APP_CANCEL_MS = 15_000

/**
 * How long a broker cancel waits before it is announced. The server reports a
 * cancel this app asked for when the broker accepts it, and the broker's own
 * update can arrive a moment before that.
 */
export const CANCEL_SETTLE_MS = 1_500

const cancelledHere = new Map<string, number>()

/** A cancel this app sent: the server's cancel_order_event or the sandbox's analyzer_update. */
export function noteCancelFromApp(orderid: unknown, now = Date.now()): void {
  const id = text(orderid)
  if (!id) return
  cancelledHere.set(id, now)
  for (const [key, at] of cancelledHere) {
    if (now - at > APP_CANCEL_MS) cancelledHere.delete(key)
  }
}

/**
 * Cancel all, sent from this app. It reaches the app as one event with no
 * order ids, so the orders it covers are the ones known to be working when it
 * went (from the books read and the frames pushed), or the ids the sandbox
 * lists. Only their cancels are its echo: an order placed after it, or
 * another order the exchange or the broker's risk checks cancel, still alerts.
 */
export function noteCancelAllFromApp(now = Date.now(), ids?: readonly unknown[]): void {
  const covered = ids && ids.length > 0 ? ids : cancelAllTargets(APP_CANCEL_MS, now)
  for (const id of covered) noteCancelFromApp(id, now)
}

function cancelledFromApp(orderid: string, now: number): boolean {
  const at = cancelledHere.get(orderid)
  return at !== undefined && now - at <= APP_CANCEL_MS
}

/**
 * Pick out the cancels in an upstream `cancel_order_event` or
 * `analyzer_update` frame. Called from the shell's own handlers for those
 * events (hooks/useSocket.ts), so no second handler is registered for them.
 */
export function noteAppCancelEvent(event: string, data: unknown, now = Date.now()): void {
  if (!data || typeof data !== 'object') return
  const frame = data as Record<string, unknown>
  if (event === 'cancel_order_event') {
    if (frame.batch_order) noteCancelAllFromApp(now)
    else noteCancelFromApp(frame.orderid, now)
    return
  }
  if (event === 'analyzer_update') {
    const request = (frame.request ?? {}) as Record<string, unknown>
    const response = (frame.response ?? {}) as Record<string, unknown>
    if (response.status !== 'success') return
    if (request.api_type === 'cancelallorder') {
      const listed = Array.isArray(response.canceled_orders) ? response.canceled_orders : []
      noteCancelAllFromApp(now, listed)
    } else if (request.api_type === 'cancelorder') {
      noteCancelFromApp(response.orderid ?? request.orderid, now)
    }
  }
}

const TOAST = {
  filled: 'success',
  partial: 'info',
  rejected: 'error',
  cancelled: 'warning',
} as const

const DURATION_MS: Record<OrderAlertKind, number> = {
  filled: 6_000,
  partial: 5_000,
  rejected: 12_000,
  cancelled: 8_000,
}

/**
 * Show an alert once: the toast, then the sound, and tell the app's other
 * tabs it has been shown. The app's alert settings decide both, as they do
 * for its other alerts: with that kind of alert, or every alert sound, turned
 * off there, nothing plays.
 */
export function announceOrderAlert(alert: OrderAlert, at = Date.now()): boolean {
  if (!shown.add(alert.key)) return false
  forgetDeferred(alert.key)
  tellOtherTabs({ key: alert.key })
  const category: keyof AlertCategories = alert.sandbox ? 'analyzer' : 'orders'
  showToast[TOAST[alert.kind]](alert.title, category, {
    description: `${istTime(at)} · ${alert.description}`,
    duration: DURATION_MS[alert.kind],
  })
  const settings = useAlertStore.getState()
  if (settings.shouldShowToast(category) && settings.shouldPlaySound()) playChime(alert.kind)
  return true
}

// ---------------------------------------------------------------------------
// One alert across the app's tabs. Every tab gets the same frame. The tab in
// use shows it at once and tells the others. A tab that is hidden, or on
// screen while another of the app's tabs is the one in use, waits a moment
// for that word before showing it itself. A tab on screen while no other tab
// is in use (the trader working in another window) shows it at once: the app
// adds no delay of its own where the trader can see it.

/** How long a tab not in use waits for the tab in use to show an alert. */
export const TAB_DEFER_MS = 400

/** What the order alerts need of a BroadcastChannel. */
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

function onTabMessage(event: MessageEvent): void {
  const data = (event.data ?? {}) as { key?: unknown; inUse?: unknown; away?: unknown }
  if (typeof data.key === 'string') {
    shown.add(data.key)
    forgetDeferred(data.key)
  }
  if (typeof data.inUse === 'string') inUse = data.inUse
  if (typeof data.away === 'string' && inUse === data.away) inUse = null
}

function tabChannel(): TabChannel | null {
  if (channel !== undefined) return channel
  try {
    channel =
      typeof BroadcastChannel === 'function' ? new BroadcastChannel('sudarshan-order-alerts') : null
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

/** Tell the other tabs when this one comes into use and when it leaves the screen. */
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

/** Whether this tab shows an alert at once rather than wait for the tab in use. */
function showsAtOnce(): boolean {
  if (typeof document === 'undefined') return true
  if (document.visibilityState === 'hidden') return false
  if (focused()) return true
  return inUse === null || inUse === TAB_ID
}

const deferred = new Map<string, ReturnType<typeof setTimeout>>()

function forgetDeferred(key: string): void {
  const timer = deferred.get(key)
  if (timer !== undefined) clearTimeout(timer)
  deferred.delete(key)
}

/** Show an alert in this tab now, or shortly unless the tab in use does. */
function deliver(alert: OrderAlert, at: number): void {
  if (shown.has(alert.key) || deferred.has(alert.key)) return
  tabChannel()
  if (showsAtOnce()) {
    announceOrderAlert(alert, at)
    return
  }
  deferred.set(
    alert.key,
    setTimeout(() => {
      deferred.delete(alert.key)
      announceOrderAlert(alert, at)
    }, TAB_DEFER_MS)
  )
}

// ---------------------------------------------------------------------------

/** Broker cancels waiting out `CANCEL_SETTLE_MS`, kept across a remount of the shell. */
const waiting = new Set<ReturnType<typeof setTimeout>>()
let installed = 0

/**
 * Listen for `order_update` on the shell's connection. Returns the function
 * that stops listening. A broker cancel still waiting is kept if the shell
 * listens again at once (a move between /trading and a normal page swaps the
 * layout in one commit), and dropped when nothing does (a logout).
 */
export function installOrderAlerts(socket: Socket): () => void {
  unlockSoundOnFirstTap()
  tabChannel()
  watchUse()
  installed += 1

  const onUpdate = (frame: OrderUpdateFrame) => {
    const at = Date.now()
    notePushedFrame(frame ?? {}, at)
    const alert = describeOrderUpdate(frame ?? {})
    if (!alert) return
    if (alert.kind !== 'cancelled') {
      deliver(alert, at)
      return
    }
    const timer = setTimeout(() => {
      waiting.delete(timer)
      if (cancelledFromApp(alert.orderid, Date.now())) shown.add(alert.key)
      else deliver(alert, at)
    }, CANCEL_SETTLE_MS)
    waiting.add(timer)
  }

  socket.on('order_update', onUpdate)
  return () => {
    socket.off('order_update', onUpdate)
    installed -= 1
    queueMicrotask(() => {
      if (installed > 0) return
      for (const timer of waiting) clearTimeout(timer)
      waiting.clear()
      for (const timer of deferred.values()) clearTimeout(timer)
      deferred.clear()
    })
  }
}

/** Tests only: forget every alert shown and every cancel noted. */
export function resetOrderAlertsForTests(): void {
  shown.clear()
  inUse = null
  cancelledHere.clear()
  for (const timer of waiting) clearTimeout(timer)
  waiting.clear()
  for (const timer of deferred.values()) clearTimeout(timer)
  deferred.clear()
  installed = 0
}
