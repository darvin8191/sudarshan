// SUDARSHAN (M5.13, M20.7): the risk layer's routes (blueprints/sudarshan_risk.py). The confirm
// phrase is sent, never read back: the server only says whether one is set.
import { webClient } from './client'

/** Where the risk guard checks orders on this server (SUDARSHAN_RISK_GUARD). */
export type RiskStage = 'off' | 'sandbox' | 'on'

export interface KillSwitchStep {
  step: string
  ok: boolean
  words: string
  detail?: Record<string, unknown>
}

export interface KillSwitchReport {
  activation_id: string
  steps: KillSwitchStep[]
  finished?: string
  /** The app restarted while the steps ran. */
  restarted?: string
}

export interface KillSwitchState {
  /** False when the server could not read the switch's state. */
  known: boolean
  on: boolean | null
  since?: string | null
  /** "HH:MM" in India. */
  since_clock?: string
  /** Where it was turned on from: app, telegram or api. */
  surface?: string | null
  actor?: string | null
  exit_all?: boolean
  /** Its steps (cancel, Exit all) are still running. */
  running?: boolean
  can_resume_at?: string | null
  report?: KillSwitchReport | null
}

export type RiskPipe = 'live' | 'sandbox'

/** The banner's read: no broker call behind it. */
export interface RiskState {
  stage: RiskStage
  kill_switch: KillSwitchState
  /** Each pipe the guard covers: whether the daily loss limit has locked it today. */
  daily?: Partial<Record<RiskPipe, { locked: boolean }>>
}

/** The daily loss card's figures for one pipe (M5.14). */
export interface DailyStatus {
  /** Today's P&L, gross; null when it could not be read just now. */
  figure: number | null
  approximate: boolean
  as_of: string
  /** The figure has reached the limit (the next new order locks the day). */
  reached: boolean
  lock: { at: string; figure: number; limit: number } | null
  /** The limit could not be checked in the last minute: orders went unchecked. */
  unchecked: boolean
}

export interface GateTiming {
  orders: number
  p50_ms: number | null
  p95_ms: number | null
  max_ms: number | null
  last_problem: { at: string; kind: string; words: string } | null
}

export interface UpstoxSegment {
  segment: string
  label: string
  /** Upstox's own kill switch, as the Upstox app sets it. SUDARSHAN never changes it. */
  on: boolean
  /** The segment is enabled on the account. */
  active: boolean
}

export interface RiskSettings {
  daily_loss_limit: number | null
  band_on: boolean
  band_opt_pct: number
  band_opt_min_rs: number
  band_fut_pct: number
  band_cash_pct: number
  band_both_sides: boolean
  size_on: boolean
  max_lots_fo: number
  max_qty_cash: number
  burst_on: boolean
  burst_n: number
  burst_window_s: number
  revoke_mcp: boolean
  phrase_set: boolean
  updated_at: string | null
  updated_by: string | null
}

export interface RiskStatus extends Omit<RiskState, 'daily'> {
  settings: RiskSettings | null
  daily?: Partial<Record<RiskPipe, DailyStatus>>
  /** Orders a second this server sends to Upstox at most (UPSTOX_ORDER_MAX_PER_SECOND). */
  pacing?: { per_second: number } | null
  gate: GateTiming
  upstox_kill_switch: UpstoxSegment[] | null
  broker: string | null
}

export interface PreviewCounts {
  orders: number
  gtts?: number | null
  stops: number
  protected: number
  positions: number
}

export interface Preview {
  counts: PreviewCounts | null
  message: string
}

export interface AuditRow {
  at: string
  last_at: string
  kind: string
  pipe: string | null
  surface: string | null
  count: number
  summary: string
}

/** The settings a card changes: numbers, switches, the limit off (null), or a new phrase. A
 * field that is not a number is sent as typed, so the server's sentence says what it takes. */
export type SettingsChange = Partial<
  Record<Exclude<keyof RiskSettings, 'phrase_set' | 'updated_at' | 'updated_by'>, unknown>
> & { confirm_phrase?: string }

/** What saving settings answered, whatever its status. */
export interface SettingsAnswer {
  code: number
  ok: boolean
  message: string
  settings: RiskSettings | null
  /** It loosens a limit: send it again with the confirm phrase. */
  needsPhrase: boolean
}

/** What turning the switch on or off answered, whatever its status. */
export interface SwitchAnswer {
  /** The HTTP status: 200, 400, 403, 409, 423, 429 or 503. */
  code: number
  ok: boolean
  message: string
  state: KillSwitchState | null
  /** This call turned it on (not "already on"), so its cancel and Exit all ran. */
  activated: boolean
}

interface Envelope<T> {
  status: string
  message?: string
  data: T
  activated?: boolean
}

const BASE = '/sudarshan/risk'

/**
 * An activation waits for orders already on their way (up to 10 s), then cancels and exits
 * through Upstox, whose calls can each take long; the page waits longer than the server can.
 */
export const SWITCH_TIMEOUT_MS = 180_000

const FALLBACK = 'SUDARSHAN could not finish this. Check Settings > Risk in a few seconds.'

async function switchCall(path: string, body: object): Promise<SwitchAnswer> {
  const response = await webClient.post<Envelope<KillSwitchState>>(`${BASE}${path}`, body, {
    timeout: SWITCH_TIMEOUT_MS,
    // Every answer but a lost session is read here, refusals included: their sentence is
    // what the sheet shows. A lost session goes to the login page as everywhere else.
    validateStatus: (status) => status !== 401,
  })
  const envelope = response.data
  const message = typeof envelope?.message === 'string' && envelope.message ? envelope.message : ''
  return {
    code: response.status,
    ok: response.status === 200,
    message: message || FALLBACK,
    state: envelope?.data && typeof envelope.data === 'object' ? envelope.data : null,
    activated: envelope?.activated === true,
  }
}

export const riskApi = {
  /** The stage and the switch: what every page's banner shows. */
  state: async (): Promise<RiskState> => {
    const response = await webClient.get<Envelope<RiskState>>(`${BASE}/state`)
    return response.data.data
  },
  status: async (): Promise<RiskStatus> => {
    const response = await webClient.get<Envelope<RiskStatus>>(`${BASE}/status`)
    return response.data.data
  },
  /** What turning it on would cancel, counted fresh from the book it acts on. */
  preview: async (): Promise<Preview> => {
    const response = await webClient.get<Envelope<Preview>>(`${BASE}/preview`)
    return response.data.data
  },
  turnOn: (exitAll: boolean, phrase: string): Promise<SwitchAnswer> =>
    switchCall('/killswitch', { exit_all: exitAll, phrase }),
  resume: (phrase: string): Promise<SwitchAnswer> => switchCall('/resume', { phrase }),
  /** Save what changed; the server says when loosening needs the confirm phrase. */
  saveSettings: async (settings: SettingsChange, phrase?: string): Promise<SettingsAnswer> => {
    const response = await webClient.put<
      Envelope<RiskSettings | null> & { needs_phrase?: boolean }
    >(`${BASE}/settings`, phrase === undefined ? { settings } : { settings, phrase }, {
      validateStatus: (status) => status !== 401,
    })
    const envelope = response.data
    const message =
      typeof envelope?.message === 'string' && envelope.message ? envelope.message : ''
    return {
      code: response.status,
      ok: response.status === 200,
      message: message || FALLBACK,
      settings: envelope?.data && typeof envelope.data === 'object' ? envelope.data : null,
      needsPhrase: envelope?.needs_phrase === true,
    }
  },
  audit: async (limit = 50): Promise<AuditRow[]> => {
    const response = await webClient.get<Envelope<AuditRow[]>>(`${BASE}/audit`, {
      params: { limit },
    })
    return response.data.data
  },
}
