// SUDARSHAN (M20.8): the System page's routes (blueprints/sudarshan_system.py). The status is
// what the server's watch last saw; nothing here reads a secret.
import { webClient } from './client'

export type CheckLevel = 'fine' | 'warning' | 'failing' | 'unread' | 'off'

/** One check of the watch (sudarshan/ops/watch/checks.py). */
export interface WatchCheck {
  name: string
  level: CheckLevel
  /** The level the owner was last told on Telegram. */
  told?: string | null
  /** Seconds since 1970 when it last changed level, when it ever did. */
  since?: number | null
  facts?: Record<string, string> | null
}

export interface WatchReport {
  /** Seconds since 1970 when the watch last looked. */
  at: number
  release: string | null
  heartbeat?: { state?: string; words?: string } | null
  checks: WatchCheck[]
  age_s: number
  /** The watch has not looked for over 10 minutes. */
  stale: boolean
}

/** The last restore drill (sudarshan/ops/drill.py). */
export interface DrillResult {
  at: string
  outcome: 'passed' | 'failed'
  /** The first thing that failed, in words; '' when it passed. */
  words?: string | null
  name?: string | null
  archive?: string | null
  databases?: number | null
}

export interface AssistantState {
  switched_on: boolean
  read_only: boolean
  write_scope_offered: boolean
  approval_required: boolean
  /** Switched on and its locks hold. */
  running: boolean
  registered: boolean
  /** The address to type into Claude; '' while it is off. */
  address: string
  words: string
}

export interface SystemStatus {
  /** Null until the watch has written its first report. */
  watch: WatchReport | null
  drill: DrillResult | null
  assistant: AssistantState
  /** The owner's signed-in devices; null when they could not be counted. */
  sessions: number | null
}

export interface DisconnectAnswer {
  ok: boolean
  message: string
}

interface Envelope<T> {
  status: string
  message?: string
  data: T
}

const BASE = '/sudarshan/system/api'
const FALLBACK =
  'SUDARSHAN could not confirm this. Turn on the kill switch on the Risk page: it ends the assistant too.'

export const systemApi = {
  status: async (): Promise<SystemStatus> => {
    const response = await webClient.get<Envelope<SystemStatus>>(`${BASE}/status`)
    return response.data.data
  },
  /** End every AI connection now. Every answer but a lost session is read: its sentence shows. */
  disconnect: async (): Promise<DisconnectAnswer> => {
    const response = await webClient.post<Envelope<unknown>>(
      `${BASE}/assistant/disconnect`,
      {},
      { validateStatus: (status) => status !== 401 }
    )
    const message = typeof response.data?.message === 'string' ? response.data.message : ''
    return { ok: response.status === 200, message: message || FALLBACK }
  },
}
