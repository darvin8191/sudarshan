import { useSyncExternalStore } from 'react'
import type { OrderUpdateFrame } from './orderAlerts'
import {
  bookStatus,
  type ClosingMatch,
  closingOrder,
  frameFitsMode,
  isFinalStatus,
  onOrderMemory,
  orderStatusOf,
} from './orderFold'

/**
 * The exits and cancels sent from the books (M6.9), kept app-wide so turning
 * the phone, going to Orders and back, or a reload inside the hold keeps them.
 *
 * Upstox's REST position book can say the old quantity for a second or more
 * after an exit, and /close_position sizes its order from that book, so a
 * second Exit in that moment reverses the position instead of closing it. So
 * a position's Exit (and Exit all) is held from the tap:
 *
 * - while the send is in flight, however long that takes;
 * - after it went, or when no answer came back (the order may have gone: "not
 *   confirmed"), until the book shows the position flat, gone or turned the
 *   other way. A part of it closing (130 becomes 65) is the exit working: the
 *   hold stays, and its clock starts again;
 * - and at most `SENT_HOLD_MS` after the answer or the last change the book
 *   showed, unless one of its own orders is still working: an order the broker
 *   pushed on the closing side since it was sent. Those are kept on the mark,
 *   so a reload keeps them too, and until a read of the order book says how
 *   they stand one counts as working. While only they hold it, the page reads
 *   the order book (exitOrders.ts), so a final status whose push was lost
 *   still settles it.
 *
 * A refusal from the server lets go at once. When the time runs out with the
 * book still unchanged, Exit is offered again and its confirmation says what
 * became of the earlier exit, as far as it is known. A cancel is held the same
 * way, until its order shows a final status (pushed or read).
 *
 * Marks are keyed by book (Live or the sandbox), kind, and the position
 * (exchange, symbol, product) or the order id.
 */

/** How long a sent exit or cancel holds its button after the answer or the book's last change. */
export const SENT_HOLD_MS = 15_000

/** How long a mark whose time ran out is kept, for the warning in the next confirmation. */
const KEEP_MS = 8 * 60 * 60_000

/** How far a read of the order book may lag an order the broker has pushed. */
const BOOK_LAG_MS = 5_000

/** The most orders kept on one exit (a sliced order is several). */
const ORDERS_KEPT = 20

const STORAGE_KEY = 'sudarshan-sent-v1'

export type SentKind = 'exit' | 'cancel'
export type SentState = 'sending' | 'sent' | 'unconfirmed'
/** What became of a send: it went, no answer came back, or the server said no. */
export type SendOutcome = 'sent' | 'unconfirmed' | 'refused'

/** An order the broker pushed on an exit's closing side since it was sent, and when first heard of. */
export interface ExitOrder {
  id: string
  at: number
}

export interface SentMark {
  kind: SentKind
  /** Sent in Analyze mode, from the sandbox's book. */
  sandbox: boolean
  /** The position's `exchange:symbol:product`, or the order id. */
  id: string
  state: SentState
  /** When the tap sent it. */
  sentAt: number
  /** When its clock last started: the answer, then each change the book showed. */
  since: number | null
  /** Its time ran out with the book unchanged: offered again, with a warning. */
  expired: boolean
  /** An exit's signed quantity when sent, and the last the book has shown since. */
  was: number | null
  seen: number | null
  /** An exit's instrument and product, for the working-order check. */
  exchange: string
  symbol: string
  product: string
  /** The send it came from, so an answer settles only its own marks. */
  batch: string
  /** An exit's own orders: pushed on its closing side since it was sent. */
  orders?: ExitOrder[]
  /** Its time is up and one of its own orders still works (or has not been read since a reload). */
  byOrder?: boolean
}

export interface SendTarget {
  id: string
  was?: number
  exchange?: string
  symbol?: string
  product?: string
}

const LOAD = Math.random().toString(36).slice(2, 8)
let sends = 0
let marks: ReadonlyMap<string, SentMark> = new Map()
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setTimeout> | null = null

function keyOf(kind: SentKind, sandbox: boolean, id: string): string {
  return `${sandbox ? 'sandbox' : 'live'}:${kind}:${id}`
}

/** A mark that holds its button now. */
export function isHeld(mark: SentMark | undefined): mark is SentMark {
  return mark !== undefined && !mark.expired
}

export function exitMark(
  all: ReadonlyMap<string, SentMark>,
  sandbox: boolean,
  id: string
): SentMark | undefined {
  return all.get(keyOf('exit', sandbox, id))
}

export function cancelMark(
  all: ReadonlyMap<string, SentMark>,
  sandbox: boolean,
  orderid: string
): SentMark | undefined {
  return all.get(keyOf('cancel', sandbox, orderid))
}

/** Every mark of one kind in one book. */
export function marksOf(
  all: ReadonlyMap<string, SentMark>,
  kind: SentKind,
  sandbox: boolean
): SentMark[] {
  return [...all.values()].filter((mark) => mark.kind === kind && mark.sandbox === sandbox)
}

function save(): void {
  try {
    if (marks.size === 0) sessionStorage.removeItem(STORAGE_KEY)
    else sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...marks.values()]))
  } catch {
    // Storage refused (private mode, quota): the marks still hold in memory.
  }
}

function valid(mark: unknown): mark is SentMark {
  if (typeof mark !== 'object' || mark === null) return false
  const m = mark as Partial<SentMark>
  return (
    (m.kind === 'exit' || m.kind === 'cancel') &&
    typeof m.sandbox === 'boolean' &&
    typeof m.id === 'string' &&
    (m.state === 'sending' || m.state === 'sent' || m.state === 'unconfirmed') &&
    typeof m.sentAt === 'number' &&
    typeof m.expired === 'boolean'
  )
}

/** The orders a stored mark names, each one well formed. */
function ordersOf(value: unknown): ExitOrder[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (order): order is ExitOrder =>
      typeof order === 'object' &&
      order !== null &&
      typeof order.id === 'string' &&
      typeof order.at === 'number'
  )
}

/** What a reload brings back: a send in flight when the page went lost its answer. */
function load(now: number): Map<string, SentMark> {
  const out = new Map<string, SentMark>()
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    const list: unknown = raw ? JSON.parse(raw) : []
    if (!Array.isArray(list)) return out
    for (const item of list) {
      if (!valid(item) || now - item.sentAt > KEEP_MS) continue
      const kept: SentMark = { ...item, orders: ordersOf(item.orders), byOrder: false }
      const mark: SentMark =
        kept.state === 'sending' ? { ...kept, state: 'unconfirmed', since: now } : kept
      out.set(keyOf(mark.kind, mark.sandbox, mark.id), mark)
    }
  } catch {
    // Nothing kept, or unreadable: start empty.
  }
  return out
}

function commit(next: Map<string, SentMark>): void {
  marks = next
  save()
  schedule()
  for (const listener of [...listeners]) listener()
}

/** Whether a mark's own time is up: never while in flight. */
function due(mark: SentMark, now: number): boolean {
  if (mark.expired || mark.state === 'sending' || mark.since === null) return false
  if (now - mark.since < SENT_HOLD_MS) return false
  return true
}

/** An exit's position and closing side, to tell its own orders by. */
function matchOf(mark: SentMark): ClosingMatch | null {
  if (mark.kind !== 'exit' || mark.was === null || mark.was === 0 || !mark.exchange) return null
  return {
    sandbox: mark.sandbox,
    exchange: mark.exchange,
    symbol: mark.symbol,
    product: mark.product,
    action: mark.was > 0 ? 'SELL' : 'BUY',
    since: mark.sentAt,
  }
}

/** Whether one of an exit's own orders still works, or has not been heard of since a reload. */
function heldByOrder(mark: SentMark): boolean {
  return (mark.orders ?? []).some(({ id }) => {
    const status = orderStatusOf(id)
    return status === null || !isFinalStatus(status)
  })
}

function expireDue(now = Date.now()): void {
  let next: Map<string, SentMark> | null = null
  for (const [key, mark] of marks) {
    if (mark.expired && now - mark.sentAt > KEEP_MS) {
      next ??= new Map(marks)
      next.delete(key)
    } else if (due(mark, now)) {
      if (heldByOrder(mark)) {
        if (mark.byOrder) continue
        next ??= new Map(marks)
        next.set(key, { ...mark, byOrder: true })
      } else {
        next ??= new Map(marks)
        next.set(key, { ...mark, expired: true, byOrder: false })
      }
    }
  }
  if (next) commit(next)
  else schedule()
}

/**
 * One timer, for the next mark due; one already past its time (a tab restored
 * after a while) at once. A mark held by its own working order has no timer:
 * it is looked at again whenever the app hears about orders.
 */
function schedule(): void {
  if (timer !== null) clearTimeout(timer)
  timer = null
  const now = Date.now()
  let first = Number.POSITIVE_INFINITY
  for (const mark of marks.values()) {
    if (mark.expired) first = Math.min(first, mark.sentAt + KEEP_MS)
    else if (!mark.byOrder && mark.state !== 'sending' && mark.since !== null) {
      first = Math.min(first, mark.since + SENT_HOLD_MS)
    }
  }
  if (first === Number.POSITIVE_INFINITY) return
  timer = setTimeout(
    () => {
      timer = null
      expireDue()
    },
    Math.max(0, first - now)
  )
}

/** Hold these rows from now: the tap. Returns the send's id, for `settleSends`. */
export function beginSends(
  kind: SentKind,
  sandbox: boolean,
  targets: SendTarget[],
  now = Date.now()
): string {
  sends += 1
  const batch = `${LOAD}.${sends}`
  const next = new Map(marks)
  for (const target of targets) {
    next.set(keyOf(kind, sandbox, target.id), {
      kind,
      sandbox,
      id: target.id,
      state: 'sending',
      sentAt: now,
      since: null,
      expired: false,
      was: target.was ?? null,
      seen: target.was ?? null,
      exchange: target.exchange ?? '',
      symbol: target.symbol ?? '',
      product: target.product ?? '',
      batch,
    })
  }
  commit(next)
  return batch
}

/** The answer came: a refusal lets go at once; sent or not confirmed, the clock starts. */
export function settleSends(batch: string, outcome: SendOutcome, now = Date.now()): void {
  let next: Map<string, SentMark> | null = null
  for (const [key, mark] of marks) {
    if (mark.batch !== batch || mark.state !== 'sending') continue
    next ??= new Map(marks)
    if (outcome === 'refused') next.delete(key)
    else next.set(key, { ...mark, state: outcome, since: now })
  }
  if (next) commit(next)
}

/** What a page's send handler returned, as an outcome: true and nothing said count as sent. */
export function outcomeOf(result: unknown): SendOutcome {
  if (result === 'refused' || result === false) return 'refused'
  if (result === 'unconfirmed') return 'unconfirmed'
  return 'sent'
}

/** Hold `targets` while `send` runs and for as long as its outcome says. */
export async function sendHeld(
  kind: SentKind,
  sandbox: boolean,
  targets: SendTarget[],
  send: () => unknown
): Promise<SendOutcome> {
  const batch = beginSends(kind, sandbox, targets)
  let outcome: SendOutcome
  try {
    outcome = outcomeOf(await send())
  } catch {
    // The handler itself broke: whether the order went is not known.
    outcome = 'unconfirmed'
  }
  settleSends(batch, outcome)
  return outcome
}

/**
 * A position book read at `bookAt`, in the sandbox's book or the broker's. A
 * book asked for before an exit was sent says nothing about it. Flat, gone or
 * turned the other way lets the exit go; any other change in its quantity is
 * the exit working: held, with its clock started again.
 */
export function noteExitBook(
  sandbox: boolean,
  rows: Array<{ id: string; quantity: number }>,
  bookAt: number,
  now = Date.now()
): void {
  const quantities = new Map(rows.map((row) => [row.id, row.quantity]))
  let next: Map<string, SentMark> | null = null
  for (const [key, mark] of marks) {
    if (mark.kind !== 'exit' || mark.sandbox !== sandbox || bookAt < mark.sentAt) continue
    const quantity = quantities.get(mark.id)
    const was = mark.was ?? quantity ?? 0
    if (quantity === undefined || quantity === 0 || Math.sign(quantity) !== Math.sign(was)) {
      next ??= new Map(marks)
      next.delete(key)
    } else if (quantity !== mark.seen) {
      next ??= new Map(marks)
      // Nearer flat is the exit working, even one whose time had run out;
      // further from it is another order (an Add), which starts nothing.
      const nearer = Math.abs(quantity) < Math.abs(mark.seen ?? was)
      next.set(
        key,
        nearer
          ? {
              ...mark,
              seen: quantity,
              since: mark.state === 'sending' ? null : now,
              expired: false,
              byOrder: false,
            }
          : { ...mark, seen: quantity }
      )
    }
  }
  if (next) commit(next)
}

/** An order book as shown: a cancel whose order is final, or gone, lets go. */
export function noteCancelBook(
  sandbox: boolean,
  orders: ReadonlyArray<{ orderid: string; order_status: string }>
): void {
  const status = new Map(orders.map((order) => [String(order.orderid), order.order_status]))
  let next: Map<string, SentMark> | null = null
  for (const [key, mark] of marks) {
    if (mark.kind !== 'cancel' || mark.sandbox !== sandbox) continue
    const now = status.get(mark.id)
    if (now === undefined || isFinalStatus(now)) {
      next ??= new Map(marks)
      next.delete(key)
    }
  }
  if (next) commit(next)
}

/**
 * A pushed order on an exit's closing side, since it was sent, is that exit's
 * own; a pushed final status ends its order's cancel; and any news of orders
 * may end an exit's wait.
 */
function onOrders(frame: OrderUpdateFrame | null): void {
  const orderid = frame ? String(frame.orderid ?? '').trim() : ''
  const status = frame ? bookStatus(frame.order_status) : null
  let next: Map<string, SentMark> | null = null
  if (frame && orderid) {
    const now = Date.now()
    for (const [key, mark] of marks) {
      const match = mark.expired ? null : matchOf(mark)
      if (!match || mark.orders?.some((order) => order.id === orderid)) continue
      if (!closingOrder(orderid, match)) continue
      next ??= new Map(marks)
      const orders = [...(mark.orders ?? []), { id: orderid, at: now }].slice(-ORDERS_KEPT)
      next.set(key, { ...mark, orders })
    }
  }
  if (frame && orderid && status && isFinalStatus(status)) {
    for (const sandbox of [false, true]) {
      const key = keyOf('cancel', sandbox, orderid)
      if (marks.has(key) && frameFitsMode(frame, sandbox ? 'analyzer' : 'live')) {
        next ??= new Map(marks)
        next.delete(key)
      }
    }
  }
  if (next) commit(next)
  expireDue()
}

/**
 * An order book read at `askedAt` for one book, the sandbox's or the
 * broker's: an exit's own order it does not list, though pushed well before
 * the read, is not working there, and stops holding the exit.
 */
export function noteExitOrders(
  sandbox: boolean,
  orders: ReadonlyArray<{ orderid: unknown }>,
  askedAt: number
): void {
  const listed = new Set(orders.map((order) => String(order.orderid ?? '').trim()))
  let next: Map<string, SentMark> | null = null
  for (const [key, mark] of marks) {
    if (mark.kind !== 'exit' || mark.sandbox !== sandbox || mark.expired || !mark.orders) continue
    const kept = mark.orders.filter(
      (order) => listed.has(order.id) || order.at > askedAt - BOOK_LAG_MS
    )
    if (kept.length === mark.orders.length) continue
    next ??= new Map(marks)
    next.set(key, { ...mark, orders: kept })
  }
  if (next) commit(next)
  expireDue()
}

/** What is known of an exit's own orders: each one's last status, pushed or read. */
export function exitOrderStatuses(mark: SentMark): string[] {
  const statuses: string[] = []
  for (const { id } of mark.orders ?? []) {
    const status = orderStatusOf(id)
    if (status !== null) statuses.push(status)
  }
  return statuses
}

onOrderMemory(onOrders)
marks = load(Date.now())
// A tab restored after a while: what is past its time is settled now, not at the next order news.
expireDue()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function snapshot(): ReadonlyMap<string, SentMark> {
  return marks
}

/** The marks as they stand, for a tap that must not wait for a redraw. */
export function sentMarksNow(): ReadonlyMap<string, SentMark> {
  return marks
}

/** Every exit and cancel held or remembered, app-wide; redraws when they change. */
export function useSentMarks(): ReadonlyMap<string, SentMark> {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Tests only: forget every mark, here and in the tab's storage. */
export function resetSentForTests(): void {
  if (timer !== null) clearTimeout(timer)
  timer = null
  marks = new Map()
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing to forget.
  }
  for (const listener of [...listeners]) listener()
}
