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
 * A frame also says how much has filled, at what average, and why an order
 * was refused. The row takes those at once, and a filled count never goes
 * down: a frame naming fewer filled than the row is older than it, and is
 * dropped whole. Upstox's REST order book carries none of them (M6.9 keeps it
 * so; see broker/upstox/mapping/order_data.py), so what the frames said fills
 * what a re-read leaves out, and rides with a held final status. Upstox's
 * REST book also says "open" for an order waiting for its trigger, so a pushed
 * "trigger pending" is kept over a re-read's "open" until a frame says
 * otherwise.
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

/** A count that may be zero: a filled or pending quantity. Null when not given. */
function count(value: unknown): number | null {
  if (text(value) === '') return null
  const n = typeof value === 'number' ? value : Number(text(value))
  return Number.isFinite(n) && n >= 0 ? n : null
}

/** An order as the book sends it, with the fill and refusal fields a frame or the sandbox's book adds. */
export type BookOrder = Order & {
  filled_quantity?: unknown
  pending_quantity?: unknown
  average_price?: unknown
  rejection_reason?: unknown
}

/** What a frame says about its order's fill and refusal: only what it carries. */
export interface FillDetail {
  filled_quantity?: number
  pending_quantity?: number
  average_price?: number
  rejection_reason?: string
}

function fillOf(frame: OrderUpdateFrame): FillDetail {
  const out: FillDetail = {}
  const filled = count(frame.filled_quantity)
  if (filled !== null) out.filled_quantity = filled
  const pending = count(frame.pending_quantity)
  if (pending !== null) out.pending_quantity = pending
  const average = positive(frame.average_price)
  if (average !== null) out.average_price = average
  const reason = text(frame.rejection_reason)
  if (reason) out.rejection_reason = reason
  return out
}

/** Whether a frame names fewer filled than already known: then it is older, all of it. */
function behind(fill: FillDetail, known: unknown): boolean {
  const had = count(known)
  return fill.filled_quantity !== undefined && had !== null && fill.filled_quantity < had
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
 * late frame is dropped whole, everything in it being older than what the row
 * says: one that would move a filled, rejected or cancelled order back to
 * working, or one naming fewer filled than the row already does.
 */
export function foldOrderUpdate(orders: Order[], frame: OrderUpdateFrame): Order[] {
  const orderid = text(frame.orderid)
  if (!orderid) return orders
  const index = orders.findIndex((order) => text(order.orderid) === orderid)
  if (index < 0) return orders
  const current: BookOrder = orders[index]
  const status = bookStatus(frame.order_status)
  if (isFinalStatus(current.order_status) && (status === null || !FINAL.has(status))) {
    return orders
  }
  const fill = fillOf(frame)
  if (behind(fill, current.filled_quantity)) return orders
  const patch: Partial<BookOrder> = { ...fill }
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

/** What the frames for an order said: its last status, when, and its fill so far. */
export interface Pushed extends FillDetail {
  order_status: string
  at: number
}

/** Orders remembered at most, oldest forgotten first: a long session must not grow these. */
const KNOWN_LIMIT = 2_000

/**
 * Remember what a frame said about its order. A final status is kept over a
 * later working one, with the time it arrived, so a frame delivered out of
 * order cannot undo a fill or end its hold early; nor can one naming fewer
 * filled. The fill builds up across frames: a cancel after a part fill keeps
 * what filled.
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
  const fill = fillOf(frame)
  if (behind(fill, before?.filled_quantity)) return
  const { order_status: _status, at: _at, ...had } = before ?? { order_status: '', at: 0 }
  pushed.delete(orderid)
  pushed.set(orderid, { ...had, ...fill, order_status: status, at: now })
  if (pushed.size > KNOWN_LIMIT) {
    const oldest = pushed.keys().next().value
    if (oldest !== undefined) pushed.delete(oldest)
  }
}

/** The fill fields a row leaves out (or, for a reason, leaves empty), from what was pushed. */
function missing(row: BookOrder, fill: FillDetail): FillDetail {
  const out: FillDetail = {}
  // A complete order filled in full: a count pushed before that is an earlier part fill.
  const complete = bookStatus(row.order_status) === 'complete'
  const partial =
    complete && fill.filled_quantity !== undefined && fill.filled_quantity < Number(row.quantity)
  if (!partial) {
    if (fill.filled_quantity !== undefined && count(row.filled_quantity) === null)
      out.filled_quantity = fill.filled_quantity
    if (fill.pending_quantity !== undefined && count(row.pending_quantity) === null)
      out.pending_quantity = fill.pending_quantity
    if (fill.average_price !== undefined && positive(row.average_price) === null)
      out.average_price = fill.average_price
  }
  if (fill.rejection_reason && !text(row.rejection_reason))
    out.rejection_reason = fill.rejection_reason
  return out
}

/**
 * A fetched book with what the frames said kept where the fetch is behind:
 *
 * - a pushed final status, with its fill, over a fetched working one for
 *   `PUSH_HOLD_MS` (a fetch that has caught up, or says something new, is
 *   taken as it is);
 * - a pushed "trigger pending" over a fetched "open", until a frame says
 *   otherwise (Upstox's REST book has no word for it);
 * - the pushed fill and refusal reason in the fields a fetched row leaves out.
 */
export function holdPushed(fetched: Order[], pushed: Map<string, Pushed>, now: number): Order[] {
  if (pushed.size === 0) return fetched
  return fetched.map((order) => {
    const entry = pushed.get(text(order.orderid))
    if (!entry) return order
    const { order_status: status, at, ...fill } = entry
    const filled = missing(order, fill)
    const row: BookOrder = Object.keys(filled).length > 0 ? { ...order, ...filled } : order
    if (FINAL.has(status) && !isFinalStatus(order.order_status) && now - at <= PUSH_HOLD_MS) {
      return { ...row, ...fill, order_status: status as Order['order_status'] }
    }
    if (status === 'trigger pending' && bookStatus(order.order_status) === 'open') {
      return { ...row, order_status: 'trigger pending' }
    }
    return row
  })
}

// ---------------------------------------------------------------------------
// What the app knows about orders, whatever page is open.

/** What the frames said, by order (the newest `KNOWN_LIMIT` orders). */
const appPushed = new Map<string, Pushed>()

/** The last frame pushed, and when it arrived. */
let lastPush: { frame: OrderUpdateFrame; at: number } | null = null

/** Each order's last known status, from the books read and the frames pushed. */
const known = new Map<string, { status: string; at: number }>()

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
  lastPush = { frame, at: now }
  rememberPushed(appPushed, frame, now)
  const orderid = text(frame.orderid)
  const status = bookStatus(frame.order_status)
  if (orderid && status) noteKnown(orderid, status, now)
}

/** The app-wide memory of what was pushed, for `holdPushed`. */
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
