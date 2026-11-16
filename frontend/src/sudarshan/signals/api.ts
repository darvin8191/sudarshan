// SUDARSHAN M13.4-M13.9 (P8 U1): the TRINETRA view through OpenAlgo's bridge
// (blueprints/sudarshan_signals.py, README section 8). One function carries every op: the route,
// the method and the class come from the contract's table (OPS in types.ts). The page reads the
// envelope's code, never the HTTP status: a success, the view switched off (a success that says
// so), or an error whose sentence is the contract's. Text from an answer is shown only when it is
// one of the contract's sentences; anything else becomes the sentence of its code, so no status
// code, exception or TRINETRA internal ever reaches the screen.
import axios from 'axios'
import { webClient } from '@/api/client'
import {
  API_PREFIX,
  type Cls,
  ERROR_STATUS,
  type ErrorCode,
  OPS,
  type OpName,
  type OpSpec,
  type SignalsOps,
  WORDS,
} from './types'

/** The page's own sentences, for what happens before any answer arrives. */
export const PAGE_WORDS = {
  unanswered: "No answer came from OpenAlgo. Check the phone's connection, then try again.",
  unsent: 'The request could not be sent. Reload the page, then try again.',
} as const

/**
 * The contract's codes, and the page's own: no answer (`unanswered`), not sent (`unsent`), an
 * answer that is not the envelope (`unreadable`), and a read made while the view is off (`off`).
 */
export type SignalsCode = ErrorCode | 'unanswered' | 'unsent' | 'unreadable' | 'off'

export interface SignalsProblem {
  code: SignalsCode
  message: string
  /** On `bad_request`: the field the sentence is about. */
  field: string | null
}

export class SignalsError extends Error {
  readonly code: SignalsCode
  readonly field: string | null
  constructor(problem: SignalsProblem) {
    super(problem.message)
    this.name = 'SignalsError'
    this.code = problem.code
    this.field = problem.field
  }
}

/** What an op returned: its data, or that the view is off (with the service's sentence). */
export type SignalsResult<T> =
  | { enabled: true; data: T }
  | { enabled: false; data: null; message: string }

/** The contract's sentences: the only text from an answer that is ever shown. */
const SENTENCES = new Set<string>(Object.values(WORDS))

/** The sentence of each code, when the answer brought none of the contract's. */
export const CODE_WORDS: Record<ErrorCode, string> = {
  unauthorized: WORDS.unauthorized,
  forbidden: WORDS.forbidden,
  rate_limited: WORDS.rate_limited,
  bad_request: WORDS.bad_request,
  not_found: WORDS.not_found,
  version_conflict: WORDS.version_conflict,
  not_ready: WORDS.not_ready,
  unavailable: WORDS.not_answering,
  not_confirmed: WORDS.not_confirmed,
}

const CODES = new Set<string>(Object.keys(ERROR_STATUS))

/** Waits with room for the bridge's own (3 s for a read, 6 s for the write) and the trip. */
const WAIT_MS: Record<Cls, number> = { read: 12_000, write: 15_000 }

const SPECS = new Map<OpName, OpSpec>(OPS.map((spec) => [spec.name, spec]))

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() !== '' ? value.trim() : null

/** A sentence from an answer, if it is one of the contract's. */
export function sentenceOf(value: unknown): string | null {
  const said = text(value)
  return said !== null && SENTENCES.has(said) ? said : null
}

function onLoginPage(url?: string): boolean {
  if (!url) return false
  try {
    return /^\/(auth\/)?login\/?$/.test(new URL(url, window.location.origin).pathname)
  } catch {
    return false
  }
}

const asText = (data: unknown): string =>
  typeof data === 'string' ? data : data && typeof data === 'object' ? JSON.stringify(data) : ''

/** The envelope's error, as the page acts on it; null when the body is not the envelope's. */
function problemFromBody(body: unknown, status: number): SignalsProblem | null {
  if (!body || typeof body !== 'object') return null
  const answer = body as { status?: unknown; error?: unknown }
  if (answer.status !== 'error' || !answer.error || typeof answer.error !== 'object') return null
  const error = answer.error as { code?: unknown; message?: unknown; field?: unknown }
  const named = text(error.code)
  const code = (
    named !== null && CODES.has(named) ? named : (codeForStatus(status) ?? 'unavailable')
  ) as ErrorCode
  return {
    code,
    message: sentenceOf(error.message) ?? CODE_WORDS[code],
    field: text(error.field),
  }
}

function codeForStatus(status: number): ErrorCode | null {
  for (const [code, each] of Object.entries(ERROR_STATUS)) {
    if (each === status) return code as ErrorCode
  }
  return null
}

/** The problem of an answer that carried no envelope: by what was asked and how it ended. */
function problemForStatus(status: number, data: unknown, cls: Cls, url?: string): SignalsProblem {
  const said = (code: ErrorCode, message: string = CODE_WORDS[code]): SignalsProblem => ({
    code,
    message,
    field: null,
  })
  if (status === 401 || onLoginPage(url)) return said('unauthorized')
  // Flask-WTF's refusal of an ended session's token reads as a 400 or 403 naming CSRF.
  if ((status === 400 || status === 403) && /csrf/i.test(asText(data))) return said('unauthorized')
  if (status === 403) return said('forbidden')
  if (status === 429) return said('rate_limited')
  if (status === 409) return said('version_conflict')
  // No such route: a server without the bridge.
  if (status === 404 || status === 501) return said('not_ready')
  // A write that may have reached the service is never said to have failed.
  if (cls === 'write') return said('not_confirmed')
  if (status === 503 || status === 504) return said('unavailable')
  return said('unavailable', WORDS.failed)
}

/** The problem in an error a request threw, in the owner's words. */
export function problemOf(error: unknown, cls: Cls = 'read'): SignalsProblem {
  if (error instanceof SignalsError) {
    return { code: error.code, message: error.message, field: error.field }
  }
  if (!axios.isAxiosError(error)) return { code: 'unsent', message: PAGE_WORDS.unsent, field: null }
  if (!error.response) {
    // Nothing came back. A write may still have arrived.
    return cls === 'write'
      ? { code: 'not_confirmed', message: CODE_WORDS.not_confirmed, field: null }
      : { code: 'unanswered', message: PAGE_WORDS.unanswered, field: null }
  }
  const { status, data, request } = error.response as {
    status: number
    data: unknown
    request?: { responseURL?: string }
  }
  return problemFromBody(data, status) ?? problemForStatus(status, data, cls, request?.responseURL)
}

function pathOf(spec: OpSpec, args: Record<string, unknown>): string {
  return `${API_PREFIX}${spec.route.replace('<id>', encodeURIComponent(String(args.id ?? '')))}`
}

function queryOf(args: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, value] of Object.entries(args)) {
    if (name === 'id' || value === undefined || value === null || value === '') continue
    out[name] = String(value)
  }
  return out
}

/** One op at the bridge. Throws a `SignalsError` in the owner's words. */
export async function signalsCall<N extends OpName>(
  name: N,
  args?: SignalsOps[N]['req'],
  signal?: AbortSignal
): Promise<SignalsResult<SignalsOps[N]['res']>> {
  const spec = SPECS.get(name)
  if (!spec) throw new SignalsError({ code: 'unsent', message: PAGE_WORDS.unsent, field: null })
  const given = (args ?? {}) as Record<string, unknown>
  const url = pathOf(spec, given)
  const cls = spec.cls
  let body: unknown
  let landed: string | undefined
  try {
    const response =
      spec.method === 'GET'
        ? await webClient.get(url, { params: queryOf(given), timeout: WAIT_MS[cls], signal })
        : await webClient.request({
            url,
            method: spec.method,
            data: given,
            timeout: WAIT_MS[cls],
            signal,
          })
    body = response.data
    landed = (response.request as { responseURL?: string } | undefined)?.responseURL
  } catch (error) {
    throw new SignalsError(problemOf(error, cls))
  }
  if (onLoginPage(landed)) {
    throw new SignalsError({ code: 'unauthorized', message: CODE_WORDS.unauthorized, field: null })
  }
  const answer = body as { status?: unknown; enabled?: unknown; data?: unknown; message?: unknown }
  if (answer && typeof answer === 'object' && answer.status === 'success') {
    if (answer.enabled === false) {
      return { enabled: false, data: null, message: sentenceOf(answer.message) ?? WORDS.off }
    }
    if (answer.data !== undefined && answer.data !== null && typeof answer.data === 'object') {
      return { enabled: true, data: answer.data as SignalsOps[N]['res'] }
    }
  }
  const coded = problemFromBody(body, 200)
  if (coded) throw new SignalsError(coded)
  throw new SignalsError(
    cls === 'write'
      ? { code: 'not_confirmed', message: CODE_WORDS.not_confirmed, field: null }
      : { code: 'unreadable', message: WORDS.failed, field: null }
  )
}

/** The data of an op, or a thrown sentence: for a part that cannot go on while the view is off. */
export async function signalsData<N extends OpName>(
  name: N,
  args?: SignalsOps[N]['req'],
  signal?: AbortSignal
): Promise<SignalsOps[N]['res']> {
  const result = await signalsCall(name, args, signal)
  if (!result.enabled) throw new SignalsError({ code: 'off', message: result.message, field: null })
  return result.data
}

/** Whether reading again may help: no answer, the service busy or not answering, or too many reads. */
export function retryable(error: unknown): boolean {
  const code = error instanceof SignalsError ? error.code : problemOf(error).code
  return code === 'unanswered' || code === 'unavailable' || code === 'rate_limited'
}
