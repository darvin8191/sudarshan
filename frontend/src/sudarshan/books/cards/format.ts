/**
 * Figures and words for the phone books (M6.9), as plain functions a test can
 * call. Prices take the exchange's decimals and no rupee sign, as a broker's
 * book shows them; money (P&L, value) takes the sign and the rupee mark from
 * the dashboard's formatters, so every screen writes a figure the same way.
 */
import { bookStatus } from '@/sudarshan/books/orderFold'
import { formatAmount, toNumber } from '@/sudarshan/dashboard/format'

const MINUS = '−'

/** A figure the broker may send as a number, a numeric string, null or not at all. */
export function figure(value: unknown): number | null {
  if (typeof value === 'number' || typeof value === 'string') return toNumber(value)
  return null
}

/** 1,050 or 0.25: a quantity, Indian grouping for whole units. */
export function units(value: number): string {
  return Number.isInteger(value) ? formatAmount(value, 0) : String(Math.abs(value))
}

/** +65 or −30: a position's quantity with its sign. */
export function signedUnits(value: number): string {
  if (value > 0) return `+${units(value)}`
  if (value < 0) return `${MINUS}${units(value)}`
  return '0'
}

/** Which way a position faces, as a broker's book says it. */
export function sideOf(quantity: number): 'LONG' | 'SHORT' | null {
  if (quantity > 0) return 'LONG'
  if (quantity < 0) return 'SHORT'
  return null
}

/** An order's status, as its chip reads and what can still be done with it. */
export interface OrderState {
  /** COMPLETE, REJECTED, CANCELLED, OPEN, TRIGGER PENDING, PENDING, or the broker's own word. */
  label: string
  tone: 'up' | 'down' | 'working' | 'muted'
  /** The filter chip it belongs to; null for a status the book does not know. */
  filter: 'open' | 'executed' | 'rejected' | 'cancelled' | null
  /** Still at the exchange, so it can be modified or cancelled. */
  working: boolean
}

/** The book's own statuses, one shared object each, so a card handed one is not redrawn for a copy. */
const STATES: Record<string, OrderState> = {
  complete: { label: 'COMPLETE', tone: 'up', filter: 'executed', working: false },
  rejected: { label: 'REJECTED', tone: 'down', filter: 'rejected', working: false },
  cancelled: { label: 'CANCELLED', tone: 'muted', filter: 'cancelled', working: false },
  open: { label: 'OPEN', tone: 'working', filter: 'open', working: true },
  'trigger pending': { label: 'TRIGGER PENDING', tone: 'working', filter: 'open', working: true },
  // Waiting for the exchange to accept it: shown as working, but nothing is
  // offered on it until the broker says it is open.
  pending: { label: 'PENDING', tone: 'working', filter: 'open', working: false },
}

/** Statuses outside the book's words, as met, for the same reason. */
const OTHER = new Map<string, OrderState>()

/**
 * The chip for an order's status. A status outside the book's own words keeps
 * the broker's word, in a neutral chip, with nothing to do on it: the order
 * book's re-read will say what it became.
 */
export function orderState(raw: string): OrderState {
  const status = bookStatus(raw)
  if (status !== null && STATES[status]) return STATES[status]
  const label =
    String(raw ?? '')
      .trim()
      .toUpperCase() || 'UNKNOWN'
  let state = OTHER.get(label)
  if (!state) {
    state = { label, tone: 'muted', filter: null, working: false }
    if (OTHER.size < 50) OTHER.set(label, state)
  }
  return state
}

/**
 * A broker's timestamp as a number to sort by, newest first. The same three
 * shapes the upstream books read: ISO and "2026-10-20 10:41:51", the Noren
 * "10:41:51 20-10-2026", and "20-10-2026 10:41:51".
 */
export function momentOf(timestamp: string | null | undefined): number {
  if (!timestamp) return 0
  let date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) {
    const noren = timestamp.match(/^(\d{2}:\d{2}:\d{2})\s+(\d{2})-(\d{2})-(\d{4})$/)
    if (noren) date = new Date(`${noren[4]}-${noren[3]}-${noren[2]}T${noren[1]}`)
  }
  if (Number.isNaN(date.getTime())) {
    const dmy = timestamp.match(/^(\d{2})-(\d{2})-(\d{4})\s+(\d{2}:\d{2}:\d{2})$/)
    if (dmy) date = new Date(`${dmy[3]}-${dmy[2]}-${dmy[1]}T${dmy[4]}`)
  }
  return date.getTime() || 0
}

/**
 * "10:41:51": the time of day a broker's timestamp carries, read from the
 * text itself, so the phone's own time zone cannot move it.
 */
export function clockOf(timestamp: string | null | undefined): string {
  if (!timestamp) return ''
  const match = String(timestamp).match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/)
  if (!match) return ''
  const [, hours, minutes, seconds] = match
  return `${hours.padStart(2, '0')}:${minutes}${seconds ? `:${seconds}` : ''}`
}
