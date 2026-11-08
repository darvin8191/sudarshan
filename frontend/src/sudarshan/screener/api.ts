// SUDARSHAN M9: the screener through OpenAlgo's bridge (blueprints/sudarshan_screener.py, design
// 6.3). One envelope for every answer; an error carries a code the page acts on and a sentence
// for the owner, shown as it came.
import axios from 'axios'
import { webClient } from '@/api/client'
import {
  type PresetCard,
  type Preview,
  type Results,
  type RunRow,
  type RunSet,
  type RunsPage,
  type ScreenerState,
  type ScreenRow,
  type ScreenSpec,
  type Today,
  type UniverseOption,
  WORDS,
} from './types'

export interface ScreenerProblem {
  /** The contract's code (`conflict`, `gone`, `invalid`, `limit`, ...) or the page's own. */
  code: string
  message: string
  /** On `invalid`: the part of the screen the sentence is about (a clause id or a field). */
  field: string | null
}

export class ScreenerError extends Error {
  readonly code: string
  readonly field: string | null
  constructor(problem: ScreenerProblem) {
    super(problem.message)
    this.name = 'ScreenerError'
    this.code = problem.code
    this.field = problem.field
  }
}

interface Envelope<T> {
  status?: 'success' | 'error'
  data?: T
  /** An error's code, sentence and the line of the screen it is about (contract.py ApiError.body). */
  error?: { code?: string; message?: string; field?: string }
  code?: string
  message?: string
  field?: string
}

/**
 * The service's error codes (contract.py ERROR_CODES) as the page acts on them. The page's own
 * names are older than the contract's, so the answer is translated once, here, and nothing else
 * reads a service code. A code with no entry (forbidden, rate_limited, bad_request, not_ready)
 * is kept: its sentence is the service's and is shown as it came.
 */
const PAGE_CODE: Record<string, string> = {
  version_conflict: 'conflict',
  refused: 'invalid',
  not_found: 'gone',
  not_confirmed: 'unanswered',
  unavailable: 'failed',
}

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null

const asText = (data: unknown): string =>
  typeof data === 'string' ? data : data && typeof data === 'object' ? JSON.stringify(data) : ''

function onLoginPage(url?: string): boolean {
  if (!url) return false
  try {
    return /^\/(auth\/)?login\/?$/.test(new URL(url, window.location.origin).pathname)
  } catch {
    return false
  }
}

/** Whether an answer says the login is over (401, a CSRF refusal, a redirect to the login page). */
export function sessionEnded(status: number | undefined, data: unknown, url?: string): boolean {
  if (status === 401) return true
  if (onLoginPage(url)) return true
  if (status === 400 || status === 403) return /csrf/i.test(asText(data))
  return false
}

/** The problem in an error a request threw, in the owner's words. */
export function problemOf(error: unknown): ScreenerProblem {
  if (error instanceof ScreenerError) {
    return { code: error.code, message: error.message, field: error.field }
  }
  if (!axios.isAxiosError(error)) return { code: 'unsent', message: WORDS.unsent, field: null }
  if (!error.response) return { code: 'unanswered', message: WORDS.unanswered, field: null }
  const { status, data, request } = error.response as {
    status: number
    data: unknown
    request?: { responseURL?: string }
  }
  const sent = data && typeof data === 'object' ? (data as Envelope<unknown>) : null
  // The service nests its refusal under `error`; an older shape put it at the top.
  const body = sent?.error && typeof sent.error === 'object' ? sent.error : sent
  const message = text(body?.message)
  if (!message) {
    if (sessionEnded(status, data, request?.responseURL)) {
      return { code: 'unauthorized', message: WORDS.session_ended, field: null }
    }
    return { code: 'unreadable', message: WORDS.unreadable, field: null }
  }
  const code = text(body?.code)
  return { code: (code && PAGE_CODE[code]) ?? code ?? 'failed', message, field: text(body?.field) }
}

const READ_MS = 15_000
const CHANGE_MS = 20_000

async function call<T>(
  request: Promise<{ data: Envelope<T>; request?: { responseURL?: string } }>
) {
  let response: { data: Envelope<T>; request?: { responseURL?: string } }
  try {
    response = await request
  } catch (error) {
    throw new ScreenerError(problemOf(error))
  }
  if (sessionEnded(undefined, null, response.request?.responseURL)) {
    throw new ScreenerError({ code: 'unauthorized', message: WORDS.session_ended, field: null })
  }
  const body = response.data
  if (body && typeof body === 'object' && body.status === 'success' && body.data !== undefined) {
    return body.data
  }
  throw new ScreenerError({ code: 'unreadable', message: WORDS.unreadable, field: null })
}

const BASE = '/sudarshan/screener/api'

export interface ScreenDraft {
  spec: ScreenSpec
  /** One id per Save, so a resend after a lost answer returns the screen already made. */
  request_id: string
  /** A screen made from a library card starts Paused: the owner reads it before it runs. */
  status?: 'paused'
}

export type ScreenChange =
  | { version: number; action: 'pause' | 'resume' }
  | { version: number; action: 'edit'; spec: ScreenSpec }

export const screenerApi = {
  state: () => call<ScreenerState>(webClient.get(`${BASE}/state`, { timeout: READ_MS })),
  create: (draft: ScreenDraft) =>
    call<ScreenRow>(webClient.post(`${BASE}/screens`, draft, { timeout: CHANGE_MS })),
  change: (id: number, change: ScreenChange) =>
    call<ScreenRow>(webClient.patch(`${BASE}/screens/${id}`, change, { timeout: CHANGE_MS })),
  remove: (id: number, removeAdded: boolean) =>
    call<{ deleted: number; removed_from_list: number }>(
      webClient.delete(`${BASE}/screens/${id}`, {
        data: { remove_added: removeAdded },
        timeout: CHANGE_MS,
      })
    ),
  /** A draft (its spec) or a saved screen (its id); six a minute. */
  preview: (target: { spec: ScreenSpec } | { screen_id: number }, signal?: AbortSignal) =>
    call<Preview>(webClient.post(`${BASE}/preview`, target, { timeout: CHANGE_MS, signal })),
  results: (id: number, signal?: AbortSignal) =>
    call<Results>(webClient.get(`${BASE}/screens/${id}/results`, { timeout: READ_MS, signal })),
  runs: (id: number, query: { before?: number | null; limit?: number }, signal?: AbortSignal) =>
    call<RunsPage>(
      webClient.get(`${BASE}/screens/${id}/runs`, {
        params: {
          ...(query.before ? { before: query.before } : {}),
          limit: query.limit ?? 30,
        },
        timeout: READ_MS,
        signal,
      })
    ),
  run: (id: number, signal?: AbortSignal) =>
    call<RunSet>(webClient.get(`${BASE}/runs/${id}`, { timeout: READ_MS, signal })),
  runsSince: (after: number) =>
    call<{ runs: RunRow[]; more: boolean }>(
      webClient.get(`${BASE}/runs/since`, { params: { after }, timeout: READ_MS })
    ),
  presets: () =>
    call<{ presets: PresetCard[] }>(webClient.get(`${BASE}/presets`, { timeout: READ_MS })),
  universes: () =>
    call<{ universes: UniverseOption[] }>(webClient.get(`${BASE}/universes`, { timeout: READ_MS })),
  today: () => call<Today>(webClient.get(`${BASE}/today`, { timeout: READ_MS })),
  membership: (query: { list?: number; screen?: number }) =>
    call<{ rows: MemberRow[] }>(
      webClient.get(`${BASE}/membership`, { params: query, timeout: READ_MS })
    ),
  /** The version is the membership row's own: a pin on a row that moved meanwhile is refused. */
  member: (id: number, version: number, action: 'pin' | 'unpin' | 'dismiss' | 'restore') =>
    call<MemberRow>(
      webClient.patch(`${BASE}/membership/${id}`, { action, version }, { timeout: CHANGE_MS })
    ),
}

export interface MemberRow {
  id: number
  screen_id: number
  list_id: number
  symbol: string
  exchange: string
  chip: import('./types').Chip | null
}

export type { RunRow }
