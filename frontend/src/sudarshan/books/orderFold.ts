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
 */

/** How long a pushed final status outranks a fetched working one. */
export const PUSH_HOLD_MS = 5_000

const FINAL = new Set([
  'complete',
  'completed',
  'filled',
  'executed',
  'rejected',
  'failed',
  'cancelled',
  'canceled',
  'expired',
  'lapsed',
])

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

function positive(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(text(value))
  return Number.isFinite(n) && n > 0 ? n : null
}

function statusOf(frame: OrderUpdateFrame): string {
  return text(frame.order_status).toLowerCase()
}

/** An order's status as it reads: final (filled, rejected, cancelled) or still working. */
export function isFinalStatus(status: string): boolean {
  return FINAL.has(status.trim().toLowerCase())
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
 * not read yet is left to the re-read, which brings it with all its fields.
 */
export function foldOrderUpdate(orders: Order[], frame: OrderUpdateFrame): Order[] {
  const orderid = text(frame.orderid)
  if (!orderid) return orders
  const index = orders.findIndex((order) => text(order.orderid) === orderid)
  if (index < 0) return orders
  const patch: Partial<Order> = {}
  const status = statusOf(frame)
  if (status) patch.order_status = status as Order['order_status']
  const quantity = positive(frame.quantity)
  if (quantity !== null) patch.quantity = quantity
  const price = positive(frame.price)
  if (price !== null) patch.price = price
  const trigger = positive(frame.trigger_price)
  if (trigger !== null) patch.trigger_price = trigger
  const next = orders.slice()
  next[index] = { ...orders[index], ...patch }
  return next
}

/** What the last frame for an order said, and when it arrived. */
export interface Pushed {
  order_status: string
  at: number
}

export function rememberPushed(
  pushed: Map<string, Pushed>,
  frame: OrderUpdateFrame,
  now: number
): void {
  const orderid = text(frame.orderid)
  const status = statusOf(frame)
  if (orderid && status) pushed.set(orderid, { order_status: status, at: now })
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
    if (!entry || !isFinalStatus(entry.order_status) || isFinalStatus(order.order_status)) {
      return order
    }
    return { ...order, order_status: entry.order_status as Order['order_status'] }
  })
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
