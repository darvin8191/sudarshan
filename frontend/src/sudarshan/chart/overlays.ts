/**
 * What is laid over the phone chart's candles (M4.7, C5): price lines and
 * bar-anchored marks, as plain data, and the pure functions that turn the
 * books into this chart's own lines.
 *
 * **The seam.** `PhoneChart.setOverlays({ lines, markers })` is the one way
 * anything is laid over the phone chart:
 *
 * - `lines` are horizontal price lines: an id, a kind, a price, a side and the
 *   words of its label. They are read-only: nothing here or in the host can
 *   drag one, and none carries a close button.
 * - `markers` are marks at a bar: an id, the bar's time in UTC seconds, a side,
 *   and optionally a price and a short text. The journal's trade markers
 *   (M14.8) arrive through this half; this file builds none.
 *
 * Each half is replaced only when the call names it, so a caller that owns the
 * markers never disturbs the lines, and the other way round. The host keeps
 * what it was given and puts it back on every chart it builds (another
 * timeframe, another style, Analyze mode), so a caller sets a half once and
 * again only when it changes.
 */
import type { AlertRow, PriceOp, PriceParams } from '@/api/marketAlerts'
import { figure, units } from '@/sudarshan/books/cards/format'
import { isFinalStatus } from '@/sudarshan/books/orderFold'
import { formatPrice } from '@/sudarshan/dashboard/format'
import type { Order, Position } from '@/types/trading'

export type LineKind = 'position' | 'order' | 'alert'
export type LineSide = 'BUY' | 'SELL'

/** One horizontal price line. */
export interface OverlayLine {
  /** Stable across updates: the same id moves its line rather than drawing another. */
  id: string
  kind: LineKind
  price: number
  /** BUY or SELL where the line has a side; null for an alert. */
  side: LineSide | null
  /** The label's words. A side is always written in them, never left to the colour. */
  label: string
  /** The book it came from could not be read in the last re-sync: drawn grey. */
  stale?: boolean
  /** A short line after the label: "as of 10:42:07" on a stale one. */
  note?: string
}

/** One mark at a bar. */
export interface OverlayMarker {
  id: string
  /** The bar's time, UTC seconds. */
  time: number
  side: LineSide
  /** Where on the bar; left out, the mark sits under a buy's bar and over a sell's. */
  price?: number
  /** A few characters beside the mark. */
  text?: string
}

/** What `setOverlays` takes: a half left out is left as it is. */
export interface Overlays {
  lines?: readonly OverlayLine[]
  markers?: readonly OverlayMarker[]
}

/** How many lines the phone chart draws at most. */
export const MAX_LINES = 12

/** What a line of this chart stands for, so its sheet can act on it (C6). */
export type LineRef =
  | { kind: 'position'; position: Position }
  | { kind: 'order'; order: Order; part: 'price' | 'trigger' }
  | { kind: 'alert'; alert: AlertRow }

/** A line and what it stands for. */
export interface ChartLine extends OverlayLine {
  ref: LineRef
}

export interface ChartLines {
  lines: ChartLine[]
  /** What the cap left out: "2 more orders · see Orders". Null when nothing was. */
  more: string | null
}

const quantityOf = (position: Position): number => Number(position.quantity) || 0

/** "Buy 65 @ 131.20": a position's side, size and average price. */
export function positionLabel(position: Position, decimals: number, withProduct = false): string {
  const quantity = quantityOf(position)
  const text = `${quantity > 0 ? 'Buy' : 'Sell'} ${units(Math.abs(quantity))} @ ${formatPrice(Number(position.average_price) || 0, decimals)}`
  return withProduct ? `${text} · ${position.product}` : text
}

/** What is left to fill, where the book says, else the order's own quantity. */
function workingQuantity(order: Order): number {
  const pending = figure((order as Order & { pending_quantity?: unknown }).pending_quantity)
  return pending !== null && pending > 0 ? pending : Number(order.quantity) || 0
}

/**
 * An order's lines: one at its limit, or at its trigger; a stop-limit order has
 * both, each saying which it is. A market order has no price to draw.
 */
export function orderLines(order: Order, decimals: number): ChartLine[] {
  const head = `${order.action} ${units(workingQuantity(order))} ${order.pricetype}`
  const limit = Number(order.price) || 0
  const trigger = Number(order.trigger_price) || 0
  const line = (part: 'price' | 'trigger', at: number, words: string): ChartLine => ({
    id: `order:${order.orderid}:${part}`,
    kind: 'order',
    price: at,
    side: order.action,
    label: `${head} ${words}${formatPrice(at, decimals)}`,
    ref: { kind: 'order', order, part },
  })
  if (order.pricetype === 'LIMIT') return limit > 0 ? [line('price', limit, '')] : []
  if (order.pricetype === 'SL-M') return trigger > 0 ? [line('trigger', trigger, '')] : []
  if (order.pricetype === 'SL') {
    const out: ChartLine[] = []
    if (trigger > 0) out.push(line('trigger', trigger, 'trigger '))
    if (limit > 0) out.push(line('price', limit, 'limit '))
    return out
  }
  return []
}

const ALERT_WORDS: Record<PriceOp, string> = {
  at_or_above: '≥',
  at_or_below: '≤',
  crosses_above: 'crosses above',
  crosses_below: 'crosses below',
  crosses: 'crosses',
}

/** "Alert ≥ 150.00": an active price alert's own line; null for any other alert. */
export function alertLine(alert: AlertRow, decimals: number): ChartLine | null {
  if (alert.kind !== 'price' || alert.status !== 'active' || alert.group_id) return null
  const params = alert.params as PriceParams
  const level = Number(params.level)
  if (!(level > 0) || !ALERT_WORDS[params.op]) return null
  return {
    id: `alert:${alert.id}`,
    kind: 'alert',
    price: level,
    side: null,
    label: `Alert ${ALERT_WORDS[params.op]} ${formatPrice(level, decimals)}`,
    ref: { kind: 'alert', alert },
  }
}

const count = (n: number, one: string, many: string) => `${n} more ${n === 1 ? one : many}`

/**
 * The charted instrument's lines, from the books as last read: every open
 * position first, then its working orders, then its active price alerts, twelve
 * at most. A book that could not be read in the last re-sync has its lines
 * marked stale, with when it was last read.
 */
export function linesOf({
  symbol,
  exchange,
  decimals,
  positions,
  orders,
  alerts,
  staleNote = null,
}: {
  symbol: string
  exchange: string
  decimals: number
  positions: readonly Position[]
  orders: readonly Order[]
  alerts: readonly AlertRow[]
  /** "as of 10:42:07" when the books' last read failed; null while they are current. */
  staleNote?: string | null
}): ChartLines {
  const here = (row: { symbol: string; exchange: string }) =>
    row.symbol === symbol && row.exchange === exchange
  const open = positions.filter(
    (p) => here(p) && quantityOf(p) !== 0 && Number(p.average_price) > 0
  )
  const held: ChartLine[] = open.map((position) => ({
    id: `position:${position.product}`,
    kind: 'position',
    price: Number(position.average_price),
    side: quantityOf(position) > 0 ? 'BUY' : 'SELL',
    label: positionLabel(position, decimals, open.length > 1),
    ref: { kind: 'position', position },
  }))
  const working = orders
    .filter((order) => here(order) && !isFinalStatus(order.order_status))
    .flatMap((order) => orderLines(order, decimals))
  const stale = (line: ChartLine): ChartLine =>
    staleNote ? { ...line, stale: true, note: staleNote } : line
  const watched = alerts
    .filter(here)
    .map((alert) => alertLine(alert, decimals))
    .filter((line): line is ChartLine => line !== null)

  const all = [...held.map(stale), ...working.map(stale), ...watched]
  const lines = all.slice(0, MAX_LINES)
  const left = all.slice(MAX_LINES)
  const leftOrders = new Set(
    left.flatMap((line) => (line.ref.kind === 'order' ? [line.ref.order.orderid] : []))
  ).size
  const leftAlerts = left.filter((line) => line.kind === 'alert').length
  const parts: string[] = []
  if (leftOrders > 0) parts.push(`${count(leftOrders, 'order', 'orders')} · see Orders`)
  if (leftAlerts > 0) parts.push(`${count(leftAlerts, 'alert', 'alerts')} · see Alerts`)
  return { lines, more: parts.length ? parts.join(' · ') : null }
}

/** A line's place on screen: its y in the chart's own pixels. */
export interface LineY {
  id: string
  y: number
}

/** Labels that share one touch target, top to bottom. */
export interface LabelCluster {
  ids: string[]
  /** The target's top and height: 48 px at least, never over the cluster below. */
  top: number
  height: number
  /** Each label's own y, for the ones drawn; a label too close to the one above is counted on it. */
  shown: Array<{ id: string; y: number; extra: number }>
}

/** A touch target's height, and the least room two drawn labels need. */
export const LABEL_TARGET = 48
export const LABEL_ROOM = 22

/**
 * Where the labels' touch targets go. Lines nearer than a target's height
 * cannot each have 48 px to themselves, so they share one target, which opens a
 * list to pick from; a line alone has a target centred on it. Within a shared
 * target a label with no room under the one above is counted on it ("+1").
 */
export function clusterLabels(lines: readonly LineY[]): LabelCluster[] {
  const sorted = [...lines].sort((a, b) => a.y - b.y)
  const groups: LineY[][] = []
  for (const line of sorted) {
    const group = groups[groups.length - 1]
    if (group && line.y - group[0].y < LABEL_TARGET) group.push(line)
    else groups.push([line])
  }
  return groups.map((group, index) => {
    const first = group[0].y
    const last = group[group.length - 1].y
    const top = first - LABEL_TARGET / 2
    const next = groups[index + 1]
    const bottom = Math.min(
      last + LABEL_TARGET / 2,
      next ? next[0].y - LABEL_TARGET / 2 : Number.POSITIVE_INFINITY
    )
    const shown: LabelCluster['shown'] = []
    for (const line of group) {
      const above = shown[shown.length - 1]
      if (above && line.y - above.y < LABEL_ROOM) above.extra += 1
      else shown.push({ id: line.id, y: line.y, extra: 0 })
    }
    return {
      ids: group.map((line) => line.id),
      top,
      height: Math.max(LABEL_TARGET, bottom - top),
      shown,
    }
  })
}
