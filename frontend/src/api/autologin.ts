// SUDARSHAN (M2.20): the Upstox auto-login settings (blueprints/sudarshan_autologin.py).
// The TOTP key and the PIN are write-only: the server reports whether they are saved and when,
// never their values.
import { webClient } from './client'

export interface SavedSecret {
  set: boolean
  updated_at: string | null
  /** False when it was saved under an encryption key the server no longer has. */
  readable: boolean
}

export type RunOutcome = 'ok' | 'failed' | 'skipped' | 'refused'

export interface AutoLoginRun {
  at: string
  kind: 'scheduled' | 'test'
  outcome: RunOutcome
  message: string
}

export interface AutoLoginAttempt {
  at: string
  kind: 'scheduled' | 'test'
  outcome: 'ok' | 'failed' | 'started'
  message: string
}

export interface AutoLoginStatus {
  enabled: boolean
  broker_supported: boolean
  connected_today: boolean
  secrets: { totp_secret: SavedSecret; pin: SavedSecret }
  paused: { message: string } | null
  last_run: AutoLoginRun | null
  last_success_at: string | null
  running: boolean
  next_run: string | null
  schedule: string[]
  test: { allowed: boolean; reason: string | null }
  recent: AutoLoginAttempt[]
}

export interface TestResult {
  outcome: RunOutcome
  message: string
  at: string | null
}

interface Envelope<T> {
  status: string
  message?: string
  data: T
}

const BASE = '/sudarshan/autologin'

export const autologinApi = {
  status: async (): Promise<AutoLoginStatus> => {
    const response = await webClient.get<Envelope<AutoLoginStatus>>(`${BASE}/status`)
    return response.data.data
  },
  saveTotpSecret: async (secret: string): Promise<string | undefined> => {
    const response = await webClient.post<Envelope<undefined>>(`${BASE}/totp-secret`, { secret })
    return response.data.message
  },
  clearTotpSecret: async (): Promise<string | undefined> => {
    const response = await webClient.delete<Envelope<undefined>>(`${BASE}/totp-secret`)
    return response.data.message
  },
  savePin: async (pin: string): Promise<string | undefined> => {
    const response = await webClient.post<Envelope<undefined>>(`${BASE}/pin`, { pin })
    return response.data.message
  },
  clearPin: async (): Promise<string | undefined> => {
    const response = await webClient.delete<Envelope<undefined>>(`${BASE}/pin`)
    return response.data.message
  },
  setEnabled: async (enabled: boolean): Promise<string | undefined> => {
    const response = await webClient.post<Envelope<undefined>>(`${BASE}/enabled`, { enabled })
    return response.data.message
  },
  /**
   * One login attempt now. Usually a few seconds; the server stops it after 150 s, so the page
   * waits longer than that (sudarshan/autologin/launcher.py TEST_TIMEOUT_S; a test compares them).
   */
  testNow: async (): Promise<TestResult> => {
    const response = await webClient.post<Envelope<TestResult>>(
      `${BASE}/test`,
      {},
      { timeout: 180_000 }
    )
    return response.data.data
  },
}
