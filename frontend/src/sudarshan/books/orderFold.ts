import { useEffect, useRef } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import type { Order } from '@/types/trading'
import type { OrderUpdateFrame } from './orderAlerts'

/**
 * The broker's order updates folded into an order book the moment they arrive
 * (M6.1): a fill, a rejection or a cancel changes its row at once, and the
 * re-read behind it (useBookSync) confirms. Upstox's REST order book can still
 * say "open" for a second or two after it has pushed the fill, so for a few
 * seconds a pushed final status outranks a fetched working one; the push is
 * never repeated, and the re-read must not be allowed to undo it.
 *
 * Frames can also arrive out of order (the server's event bus dispatches them
 * from a thread pool), so a final status is never moved back to a working one
 * by a frame, and a status outside the order book's own words ("not
 * cancelled", "validation pending") changes nothing: the row keeps the status
 * it had, with its Cancel and Modify, until the next read says otherwise.
 *
 * What was pushed is also kept app-wide, fed by the shell's order alert
 * listener, which is installed whatever page is open. A page opened a moment
 * after a fill then applies the same hold to its first read and asks once more
 * when the broker's book should have caught up (useBookSync).
 */

/** How long a pushed final status outranks a fetched working one. */
export const PUSH_HOLD_MS = 5_000

/** The order book's own words for an order's status, and the broker spellings they cover. */
const STATUS_WORDS: Record<string, Order['order_status']> = {
  open: 'open',
  pending: 'pending',
  'trigger pending': 'trigger pending',
  complete: 'complete',
  completed: 'complete',
  filled: 'complete',
  executed: 'complete',
  traded: 'complete',
  rejected: 'rejected',
  failed: 'rejected',
  cancelled: 'cancelled',
  canceled: 'cancelled',
  expired: 'cancelled',
  lapsed: 'cancelled',
}

const FINAL = new Set<string>(['complete', 'rejected', 'cancelled'])

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

function positive(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(text(value))
  return Number.isFinite(n) && n > 0 ? n : null
}

/** A frame's status in the order book's words, or null for one it does not use. */
export function bookStatus(raw: unknown): Order['order_status'] | null {
  return STATUS_WORDS[text(raw).toLowerCase()] ?? null
}

/** An order's status as it reads: final (filled, rejected, cancelled) or still working. */
export function isFinalStatus(status: string): boolean {
  const word = bookStatus(status)
  return word !== null && FINAL.has(word)
}

/**
 * Whether a frame belongs to the book on screen: a Live book takes the
 * broker's frames, an Analyze book the sandbox's. A frame that names no mode
 * is taken by both.
 */
export function frameFitsMode(frame: OrderUpdateFrame, appMode: 'live' | 'analyzer'): boolean {
  if (frame.mode === 'live') return appMode === 'live'
  if (frame.mode === 'analyze' || frame.mode === 'analyzer') return appMode === 'analyzer'
  return true
}

/**
 * A book with one frame applied to the order it names. An order the book has
 * not read yet is left to the re-read, which brings it with all its fields. A
 * late frame that would move a filled, rejected or cancelled order back to
 * working is dropped whole: everything in it is older than what the row says.
 */
export function foldOrderUpdate(orders: Order[], frame: OrderUpdateFrame): Order[] {
  const orderid = text(frame.orderid)
  if (!orderid) return orders
  const index = orders.findIndex((order) => text(order.orderid) === orderid)
  if (index < 0) return orders
  const current = orders[index]
  const status = bookStatus(frame.order_status)
  if (isFinalStatus(current.order_status) && (status === null || !FINAL.has(status))) {
    return orders
  }
  const patch: Partial<Order> = {}
  if (status) patch.order_status = status
  const quantity = positive(frame.quantity)
  if (quantity !== null) patch.quantity = quantity
  const price = positive(frame.price)
  if (price !== null) patch.price = price
  const trigger = positive(frame.trigger_price)
  if (trigger !== null) patch.trigger_price = trigger
  const next = orders.slice()
  next[index] = { ...current, ...patch }
  return next
}

/** What the last frame for an order said, and when it arrived. */
export interface Pushed {
  order_status: string
  at: number
}

/**
 * Remember what a frame said about its order. A final status is kept over a
 * later working one, with the time it arrived, so a frame delivered out of
 * order cannot undo a fill or end its hold early.
 */
export function rememberPushed(
  pushed: Map<string, Pushed>,
  frame: OrderUpdateFrame,
  now: number
): void {
  const orderid = text(frame.orderid)
  const status = bookStatus(frame.order_status)
  if (!orderid || !status) return
  const before = pushed.get(orderid)
  if (before && FINAL.has(before.order_status) && !FINAL.has(status)) return
  pushed.set(orderid, { order_status: status, at: now })
}

/**
 * A fetched book with each pushed final status kept over a fetched working
 * one for `PUSH_HOLD_MS`. A fetch that has caught up, or says something new,
 * is taken as it is; older entries are forgotten on the way.
 */
export function holdPushed(fetched: Order[], pushed: Map<string, Pushed>, now: number): Order[] {
  for (const [orderid, entry] of pushed) {
    if (now - entry.at > PUSH_HOLD_MS) pushed.delete(orderid)
  }
  if (pushed.size === 0) return fetched
  return fetched.map((order) => {
    const entry = pushed.get(text(order.orderid))
    if (!entry || !FINAL.has(entry.order_status) || isFinalStatus(order.order_status)) {
      return order
    }
    return { ...order, order_status: entry.order_status as Order['order_status'] }
  })
}

// ---------------------------------------------------------------------------
// What the app knows about orders, whatever page is open.

/** The frames pushed in the last `PUSH_HOLD_MS`, by order. */
const appPushed = new Map<string, Pushed>()

/** The last frame pushed, and when it arrived. */
let lastPush: { frame: OrderUpdateFrame; at: number } | null = null

/** Each order's last known status, from the books read and the frames pushed. */
const known = new Map<string, { status: string; at: number }>()

/** Orders remembered at most, oldest forgotten first: a long session must not grow this. */
const KNOWN_LIMIT = 2_000

function noteKnown(orderid: string, status: string, at: number): void {
  const before = known.get(orderid)
  if (before && FINAL.has(before.status) && !FINAL.has(status) && before.at >= at - PUSH_HOLD_MS) {
    // A late working status, or a lagging read, does not reopen a final one.
    return
  }
  known.delete(orderid)
  known.set(orderid, { status, at })
  if (known.size > KNOWN_LIMIT) {
    const oldest = known.keys().next().value
    if (oldest !== undefined) known.delete(oldest)
  }
}

/** Record a pushed frame. Called for every `order_update` by the shell's listener. */
export function notePushedFrame(frame: OrderUpdateFrame, now = Date.now()): void {
  for (const [orderid, entry] of appPushed) {
    if (now - entry.at > PUSH_HOLD_MS) appPushed.delete(orderid)
  }
  lastPush = { frame, at: now }
  rememberPushed(appPushed, frame, now)
  const orderid = text(frame.orderid)
  const status = bookStatus(frame.order_status)
  if (orderid && status) noteKnown(orderid, status, now)
}

/** The app-wide memory of pushed statuses, for `holdPushed`. */
export function pushedStatuses(): Map<string, Pushed> {
  return appPushed
}

/** The last frame pushed and when, if one came in the last `withinMs`. */
export function recentPush(
  withinMs: number,
  now = Date.now()
): { frame: OrderUpdateFrame; at: number } | null {
  return lastPush && now - lastPush.at <= withinMs ? lastPush : null
}

/** Record an order book as read from the broker. */
export function noteOrderBook(orders: readonly Order[], at = Date.now()): void {
  for (const order of orders) {
    const orderid = text(order.orderid)
    const status = bookStatus(order.order_status)
    if (orderid && status) noteKnown(orderid, status, at)
  }
}

/** The orders last known to be working: what a cancel-all sent now would cancel. */
export function workingOrderIds(): string[] {
  const ids: string[] = []
  for (const [orderid, entry] of known) if (!FINAL.has(entry.status)) ids.push(orderid)
  return ids
}

/**
 * The orders a cancel-all just reported would have cancelled: those still
 * working, and those whose cancel the broker pushed in the last `withinMs`,
 * since its echoes can arrive before the app hears that the cancel-all went
 * through.
 */
export function cancelAllTargets(withinMs: number, now = Date.now()): string[] {
  const ids: string[] = []
  for (const [orderid, entry] of known) {
    if (!FINAL.has(entry.status) || (entry.status === 'cancelled' && now - entry.at <= withinMs)) {
      ids.push(orderid)
    }
  }
  return ids
}

/** Tests only: forget everything pushed and read. */
export function resetOrderMemoryForTests(): void {
  appPushed.clear()
  known.clear()
  lastPush = null
}

/** Call `onFrame` with every `order_update` on the shell's connection while mounted. */
export function useOrderUpdates(onFrame: (frame: OrderUpdateFrame) => void, enabled = true): void {
  const { socket } = useSocketContext()
  const handler = useRef(onFrame)
  useEffect(() => {
    handler.current = onFrame
  }, [onFrame])
  useEffect(() => {
    if (!enabled || !socket) return
    const onUpdate = (frame: OrderUpdateFrame) => handler.current(frame ?? {})
    socket.on('order_update', onUpdate)
    return () => {
      socket.off('order_update', onUpdate)
    }
  }, [socket, enabled])
}
