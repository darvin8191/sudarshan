// SUDARSHAN M10: market alerts, through OpenAlgo's bridge (blueprints/sudarshan_alerts.py), typed
// to the API contract (version 3: AlertEvent's detail and note, the delivery push). Every answer
// is the envelope below. On an error its `message` is already in the owner's words and is shown
// as it is; `code` is what the page acts on.
import axios from 'axios'
import { webClient } from './client'

export type AlertExchange = 'NSE' | 'BSE' | 'NFO' | 'BFO' | 'NSE_INDEX' | 'BSE_INDEX'
export type Segment = 'NSE_EQ' | 'BSE_EQ' | 'NSE_FO' | 'BSE_FO' | 'NSE_INDEX' | 'BSE_INDEX'

/** A line's state: a session's, an alert's own (cards only), or the service's (the engine only). */
export type LineCode =
  | 'ok'
  | 'closing_auction'
  | 'feed_down'
  | 'not_open_yet'
  | 'closed_by_exchange'
  | 'outside_hours'
  | 'closed_today'
  | 'waiting'
  | 'note'
  | 'starting'
  | 'off'

export interface StatusLine {
  code: LineCode
  line: string
  /** True while ticks arrive: 'ok', 'waiting', 'note'. */
  real_time: boolean
}

export interface Prefs {
  telegram: boolean
  app: boolean
}

export interface Limits {
  instruments: number
  instruments_used: number
  active: number
  active_used: number
}

export type AlertStatus = 'active' | 'paused' | 'stopped'
export type AlertKind = 'price' | 'move' | 'day_break' | 'vwap'
export type Frequency = 'once' | 'per_candle' | 'at_close' | 'every_time'
export type CandleMin = 1 | 5 | 15 | 30 | 60 | 0
export type PriceOp = 'crosses_above' | 'crosses_below' | 'crosses' | 'at_or_above' | 'at_or_below'
export type PriceSource = 'ltp' | 'bid' | 'ask'

export interface PriceParams {
  op: PriceOp
  level: string
  source?: PriceSource
}
export interface MoveParams {
  base?: 'close' | 'open'
  direction: 'up' | 'down' | 'either'
  percent: string
}
export interface BreakParams {
  side: 'high' | 'low'
  after?: string
}
export interface VwapParams {
  direction: 'above' | 'below' | 'either'
}
export type Params = PriceParams | MoveParams | BreakParams | VwapParams

export interface AlertRow {
  id: number
  /** Sent back with every PATCH. */
  version: number
  status: AlertStatus
  /** '' or words: 'Fired once at 10:42 on 21 Oct'. */
  status_reason: string
  kind: AlertKind
  symbol: string
  exchange: AlertExchange
  /** The Upstox key; its prefix names the alert's segment in `segments`. */
  instrument_key: string
  tick_size: string
  expiry: string | null
  params: Params
  frequency: Frequency
  /** 0 is the day; null unless per_candle or at_close. */
  candle_min: CandleMin | null
  cooldown_s: number
  ends_at: number | null
  note: string
  /** Null: the settings decide. */
  channels: Prefs | null
  created_at: number
  updated_at: number
  words: { condition: string; meta: string; checks_at: string | null }
  /** Active alerts only, and only when not simply watching. */
  check: StatusLine | null
  fired_today: number
  auction: { from: number; close: number; until: number } | null
}

export interface MarketAlertsState {
  /** False: switched off on the server (then `alerts` is empty). */
  enabled: boolean
  engine: StatusLine
  segments: Partial<Record<Segment, StatusLine>>
  limits: Limits
  prefs: Prefs
  alerts: AlertRow[]
  /** The newest event's id (0: none). */
  last_event_id: number
  server_time: number
}

export type Candle = 1 | 5 | 15 | 30 | 60 | 'day'

export interface AlertDraft {
  symbol: string
  exchange: AlertExchange
  kind: AlertKind
  params: Params
  frequency?: Frequency
  /** per_candle and at_close only. */
  candle?: Candle
  cooldown_s?: number
  /** 'YYYY-MM-DD' (works through that day), an ISO datetime, or null (no end). */
  ends?: string | null
  note?: string
  channels?: Prefs | null
  /** The last price the form shows. */
  price?: number
}

export type Change =
  | { version: number; action: 'edit'; alert: AlertDraft }
  | { version: number; action: 'pause' | 'resume' | 'restart' }

export type Channel = 'telegram' | 'app'

export interface Delivery {
  status: 'pending' | 'sent' | 'not_sent' | 'off'
  reason: string
  attempts: number
  updated_at: number
}

export interface AlertEvent {
  id: number
  /** 0 for a test. */
  alert_id: number
  at: number
  trade_time: number | null
  symbol: string
  exchange: string
  kind: string
  title: string
  /** The toast's detail, frozen at fire time (contract 3): 'now ₹100.05'. */
  detail: string
  /** The owner's note, frozen at fire time; '' when none. */
  note: string
  /** The Telegram line, frozen at fire time. */
  line: string
  value: string | null
  level: string | null
  found_later: boolean
  opening: boolean
  test: boolean
  deliveries: Partial<Record<Channel, Delivery>>
}

export interface EventsPage {
  events: AlertEvent[]
  /** An older page (History) or a newer one (events/since) is still there. */
  more: boolean
}

export interface TestResult {
  event_id: number
  deliveries: Partial<Record<Channel, Delivery>>
}

/** `sudarshan_market_alert`: one fired alert, or a test. */
export interface MarketAlertPush {
  event_id: number
  alert_id: number
  symbol: string
  exchange: string
  title: string
  detail: string
  note: string
  at: string
  at_ms: number
  found_later: boolean
  opening: boolean
  auction: boolean
  test: boolean
}

/** `sudarshan_market_alert_delivery` (contract 3): one channel's delivery of an event settled. */
export interface MarketAlertDeliveryPush {
  event_id: number
  channel: Channel
  delivery: Delivery
}

/** `sudarshan_market_alerts_state`: the engine line and the alerts whose status changed. */
export interface MarketAlertsStatePush {
  engine: StatusLine
  alerts: Array<
    Pick<AlertRow, 'id' | 'version' | 'status' | 'status_reason' | 'check' | 'fired_today'>
  >
  removed: number[]
  server_time: number
}

interface Envelope<T> {
  status: 'success' | 'error'
  data?: T
  code?: string
  message?: string
  field?: string
}

/** What went wrong, as the page acts on it. */
export interface AlertsProblem {
  /**
   * The contract's code, or one of the page's own: `unanswered` when no answer came (a change may
   * or may not have been made), `unsent` when the request never left, `unreadable` when the
   * answer was not the bridge's.
   */
  code: string
  message: string
  /** On a 400: the form field to mark. */
  field: string | null
}

export class AlertsError extends Error {
  readonly code: string
  readonly field: string | null

  constructor(problem: AlertsProblem) {
    super(problem.message)
    this.name = 'AlertsError'
    this.code = problem.code
    this.field = problem.field
  }
}

const NO_ANSWER = 'No answer came from OpenAlgo. Check the connection, then try again.'
const NOT_SENT = 'The request could not be sent. Reload the page, then try again.'
const UNREADABLE = 'OpenAlgo’s answer could not be read. Try again in a minute.'

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** The problem in an error a request threw, in the owner's words. */
export function problemOf(error: unknown): AlertsProblem {
  if (error instanceof AlertsError) {
    return { code: error.code, message: error.message, field: error.field }
  }
  if (!axios.isAxiosError(error)) return { code: 'unsent', message: NOT_SENT, field: null }
  if (!error.response) return { code: 'unanswered', message: NO_ANSWER, field: null }
  const body = error.response.data as Partial<Envelope<unknown>> | null
  const message = body && typeof body === 'object' ? text(body.message) : null
  if (!message) return { code: 'unreadable', message: UNREADABLE, field: null }
  return { code: text(body?.code) ?? 'failed', message, field: text(body?.field) }
}

/** A read waits this long: the bridge gives the service 3 s. */
const READ_MS = 15_000
/** A change waits this long: the bridge gives the service 6 s. */
const CHANGE_MS = 20_000
/** The test waits this long: the service waits up to 10 s for Telegram, the bridge 12 s. */
const TEST_MS = 30_000

async function call<T>(request: Promise<{ data: Envelope<T> }>): Promise<T> {
  let body: Envelope<T>
  try {
    body = (await request).data
  } catch (error) {
    throw new AlertsError(problemOf(error))
  }
  if (body && typeof body === 'object' && body.status === 'success' && body.data !== undefined) {
    return body.data
  }
  throw new AlertsError({ code: 'unreadable', message: UNREADABLE, field: null })
}

const BASE = '/sudarshan/alerts/api'

export const marketAlertsApi = {
  state: () => call<MarketAlertsState>(webClient.get(`${BASE}/state`, { timeout: READ_MS })),
  /** History, newest first, one page at a time: `before` the last id of the page before. */
  events: (query: { before?: number | null; limit?: number; alert?: number }) =>
    call<EventsPage>(
      webClient.get(`${BASE}/events`, {
        params: {
          ...(query.before ? { before: query.before } : {}),
          limit: query.limit ?? 50,
          ...(query.alert ? { alert: query.alert } : {}),
        },
        timeout: READ_MS,
      })
    ),
  /** What fired after event `after`, oldest first. */
  eventsSince: (after: number, limit = 200) =>
    call<EventsPage>(
      webClient.get(`${BASE}/events/since`, { params: { after, limit }, timeout: READ_MS })
    ),
  create: (alert: AlertDraft) =>
    call<AlertRow>(webClient.post(`${BASE}/alerts`, { alert }, { timeout: CHANGE_MS })),
  change: (id: number, change: Change) =>
    call<AlertRow>(webClient.patch(`${BASE}/alerts/${id}`, change, { timeout: CHANGE_MS })),
  remove: (id: number) =>
    call<{ deleted: boolean }>(webClient.delete(`${BASE}/alerts/${id}`, { timeout: CHANGE_MS })),
  /** Where alerts go when an alert does not say: the default for every alert, on every device. */
  prefs: () => call<Prefs>(webClient.get(`${BASE}/prefs`, { timeout: READ_MS })),
  setPrefs: (prefs: Prefs) =>
    call<Prefs>(
      webClient.put(
        `${BASE}/prefs`,
        { telegram: prefs.telegram, app: prefs.app },
        { timeout: CHANGE_MS }
      )
    ),
  /** One test through each channel that is on; Telegram's answer is waited for up to 10 s. */
  test: () => call<TestResult>(webClient.post(`${BASE}/test`, {}, { timeout: TEST_MS })),
}
