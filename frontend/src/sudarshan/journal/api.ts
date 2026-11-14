// SUDARSHAN M14, JD1: the journal, through OpenAlgo's bridge (/sudarshan/journal/api/*).
// One function carries every op: the route, the method and the class of op come from the
// contract's table (OPS in types.ts), so a later op never means editing this file. A success is
// the contract's envelope; switched off is a success that says so; an error carries a sentence
// for the owner, shown as the bridge wrote it. Nothing here shows a status code or a class name.
import axios from 'axios'
import { webClient } from '@/api/client'
import {
  API_PREFIX,
  type ChargeActual,
  type ChargeEstimate,
  type Cls,
  type Done,
  type Excursion,
  type Fill,
  type JournalOps,
  OPS,
  type OpName,
  type OpSpec,
  type Trip,
  type TripNote,
} from './types'

/**
 * A fill on the trade sheet: as executed, and `trip_qty`, how much of it is this trade's (a
 * reversal's fill closes one trade and opens the next). An older service sends no `trip_qty`.
 */
export type SheetFill = Fill & { trip_qty?: number }

/** The detail the trade sheet draws. The contract's `Trip` is the row; the rest rides beside it. */
export interface TripDetail extends Trip {
  fills?: SheetFill[]
  note?: TripNote | null
  excursion?: Excursion | null
  charges?: ChargeEstimate | null
  /** Upstox's own charges as this trade carries them; an older service sends none. */
  charges_actual?: ChargeActual | null
  /**
   * Why the trade still carries the estimate, when the owner could not tell: Upstox gave only
   * the month's total and the journal does not cover the whole month. The service's sentence.
   */
  charges_note?: string | null
  /**
   * "First entry order: answered in 412 ms": the round trip of the trade's first entry order, when
   * the platform recorded one (the service's `latency_ms`); never a later order's.
   */
  latency_ms?: number | null
}

/** What a trip's detail answers: the contract says `Trip`; the detail is a superset of it. */
export type JournalAnswers = Omit<JournalOps, 'trip_get'> & {
  trip_get: { req: JournalOps['trip_get']['req']; res: TripDetail }
}

/** The words of this module, in one place (design 6.4): every one a sentence with a next step. */
export const WORDS = {
  off: 'The journal is switched off on the server.',
  unavailable: 'The journal service is not answering. Your trades are safe; try again in a minute.',
  notReady: 'This part of the journal is not ready yet.',
  noAnswer: 'No answer came from OpenAlgo. Check the connection, then try again.',
  notSent: 'The request could not be sent. Reload the page, then try again.',
  unreadable: 'OpenAlgo’s answer could not be read. Try again in a minute.',
  signedOut: 'Your session has ended. Sign in again, then come back to the journal.',
  /** Any 409 from the bridge, until the sheet that asked says it in its own words. */
  changedElsewhere: 'This was changed on another screen. Nothing was saved; open it again.',
  conflict: 'This trade’s notes were changed on another screen. Nothing was saved; open it again.',
  playbookConflict:
    'This playbook was changed on another screen. Nothing was saved; open it again.',
} as const

export type ProblemCode =
  | 'unauthorized'
  | 'forbidden'
  | 'conflict'
  | 'busy'
  | 'unanswered'
  | 'unsent'
  | 'unreadable'
  | 'unavailable'
  | 'refused'

/** A sentence for the owner and the code the page acts on. */
export interface JournalProblem {
  code: ProblemCode
  message: string
}

export class JournalError extends Error {
  readonly code: ProblemCode
  constructor(problem: JournalProblem) {
    super(problem.message)
    this.name = 'JournalError'
    this.code = problem.code
  }
}

/** What an op returned: its data, or that the journal is switched off (with the sentence). */
export type JournalResult<T> =
  | { enabled: true; data: T }
  | { enabled: false; data: null; message: string }

/** Waits with room for the bridge's own waits and the trip. */
const WAIT_MS: Record<Cls, number> = {
  read: 15_000,
  write: 20_000,
  download: 30_000,
  reconcile: 30_000,
}

const SPECS = new Map<OpName, OpSpec>(OPS.map((spec) => [spec.name, spec]))

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null

/** The words of a status the bridge answered without a sentence of its own. */
function problemForStatus(status: number): JournalProblem {
  if (status === 401) return { code: 'unauthorized', message: WORDS.signedOut }
  if (status === 403) {
    return {
      code: 'forbidden',
      message: 'The journal is for the owner’s sign-in only. Sign in as the owner to see it.',
    }
  }
  if (status === 409) return { code: 'conflict', message: WORDS.changedElsewhere }
  if (status === 429) {
    return {
      code: 'busy',
      message: 'That was too many requests in a minute. Wait a little, then try again.',
    }
  }
  if (status === 404 || status === 501) return { code: 'unavailable', message: WORDS.notReady }
  return { code: 'unavailable', message: WORDS.unavailable }
}

/**
 * The bridge's codes the page acts on, by the bridge's own names (blueprints/sudarshan_journal.py
 * ERROR_STATUS), so a conflict is known by its code and not only by its status (review P8).
 */
const KNOWN_CODES: Record<string, ProblemCode> = {
  conflict: 'conflict',
  version_conflict: 'conflict',
  busy: 'busy',
  rate_limited: 'busy',
  unauthorized: 'unauthorized',
  forbidden: 'forbidden',
}

function problemFrom(body: unknown): JournalProblem | null {
  if (!body || typeof body !== 'object') return null
  const raw = (body as { error?: unknown }).error
  const message =
    typeof raw === 'string'
      ? text(raw)
      : raw && typeof raw === 'object'
        ? text((raw as { message?: unknown }).message)
        : text((body as { message?: unknown }).message)
  if (!message) return null
  const code = raw && typeof raw === 'object' ? text((raw as { code?: unknown }).code) : null
  return { code: KNOWN_CODES[code ?? ''] ?? 'refused', message }
}

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

/** Whether an answer says the login is over: a 401, a CSRF refusal, or a redirect to the login. */
export function sessionEnded(status: number | undefined, data: unknown, url?: string): boolean {
  if (status === 401) return true
  if (onLoginPage(url)) return true
  if (status === 400 || status === 403) return /csrf/i.test(asText(data))
  return false
}

/** The problem in an error a request threw, in the owner's words. */
export function problemOf(error: unknown): JournalProblem {
  if (error instanceof JournalError) return { code: error.code, message: error.message }
  if (!axios.isAxiosError(error)) return { code: 'unsent', message: WORDS.notSent }
  if (!error.response) return { code: 'unanswered', message: WORDS.noAnswer }
  const { status, data, request } = error.response as {
    status: number
    data: unknown
    request?: { responseURL?: string }
  }
  const coded = problemFrom(data)
  if (coded === null && sessionEnded(status, data, request?.responseURL)) {
    return { code: 'unauthorized', message: WORDS.signedOut }
  }
  if (coded) {
    // A conflict is said in the one sentence the owner learns; the sheet that asked words it.
    return status === 409 ? { code: 'conflict', message: WORDS.changedElsewhere } : coded
  }
  return problemForStatus(status)
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

function bodyOf(args: Record<string, unknown>): Record<string, unknown> {
  const { id: _target, ...rest } = args
  return rest
}

/**
 * The address a download is opened from (the bridge's file route); the session is the key.
 * `csv` asks for a monthly statement's trades as its CSV instead of the report's own file.
 */
export function reportFileUrl(id: number, format?: 'csv'): string {
  const spec = SPECS.get('report_file')
  if (!spec) return ''
  const path = pathOf(spec, { id })
  return format === 'csv' ? `${path}?format=csv` : path
}

/** The types a report's file comes as (cross-design Y32). */
const FILE_TYPES = new Set(['application/pdf', 'text/csv', 'image/png'])

/** A file that came back as something no phone can open: said, never saved. */
export const FILE_UNREADABLE =
  'This report’s file could not be read. Its text is on the Reports page; try the download again later.'

const mimeOf = (header: unknown): string =>
  typeof header === 'string' ? (header.split(';')[0] ?? '').trim().toLowerCase() : ''

/** A blob's text: `Blob.text` where the browser has it, a reader where it does not. */
function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

/** The bridge's envelope in a blob, or null when it holds none. */
async function envelopeOf(data: unknown): Promise<unknown> {
  if (!(data instanceof Blob)) return data
  if (data.size > 64 * 1024) return null
  try {
    return JSON.parse(await blobText(data))
  } catch {
    return null
  }
}

/**
 * A report's file, fetched rather than opened as a link (review H4), so nothing that will not open
 * is ever saved: a 200 of a PDF, CSV or PNG is the file; anything else, a refusal or a 200 holding
 * the bridge's envelope (the journal switched off, from a bridge before this release), is thrown as
 * a `JournalError` with the sentence the bridge wrote. A 401 also takes the app's own way back to
 * the sign-in (the web client's).
 */
export async function reportFile(id: number, format?: 'csv', signal?: AbortSignal): Promise<Blob> {
  const url = reportFileUrl(id, format)
  let response: { data: unknown; headers?: Record<string, unknown>; request?: unknown }
  try {
    response = await webClient.get(url, {
      responseType: 'blob',
      timeout: WAIT_MS.download,
      signal,
    })
  } catch (error) {
    if (axios.isAxiosError(error) && error.response) {
      error.response.data = await envelopeOf(error.response.data)
    }
    throw new JournalError(problemOf(error))
  }
  const landed = (response.request as { responseURL?: string } | undefined)?.responseURL
  if (sessionEnded(undefined, null, landed)) {
    throw new JournalError({ code: 'unauthorized', message: WORDS.signedOut })
  }
  const type = mimeOf(response.headers?.['content-type'])
  const data = response.data
  if (data instanceof Blob && FILE_TYPES.has(type) && (format !== 'csv' || type === 'text/csv')) {
    return data.type === type ? data : new Blob([data], { type })
  }
  const body = (await envelopeOf(data)) as {
    status?: unknown
    enabled?: unknown
    message?: unknown
  }
  if (body && typeof body === 'object' && body.status === 'success' && body.enabled === false) {
    throw new JournalError({ code: 'unavailable', message: text(body.message) ?? WORDS.off })
  }
  throw new JournalError(problemFrom(body) ?? { code: 'unreadable', message: FILE_UNREADABLE })
}

/**
 * How long a saved file's address lives after the tap: some browsers start the download after the
 * click returns, and cancel it when the address is already gone (as watchlist/tvTransfer.ts).
 */
export const FILE_URL_MS = 60_000

/** Give the owner the file under `name`. */
export function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), FILE_URL_MS)
}

/** One op at the bridge. Throws a `JournalError` in the owner's words. */
export async function journalCall<N extends Exclude<OpName, 'delivery' | 'family_day'>>(
  name: N,
  args?: JournalAnswers[N]['req'],
  signal?: AbortSignal
): Promise<JournalResult<JournalAnswers[N]['res']>> {
  const spec = SPECS.get(name)
  if (!spec) throw new JournalError({ code: 'unsent', message: WORDS.notSent })
  const given = (args ?? {}) as Record<string, unknown>
  const url = pathOf(spec, given)
  const timeout = WAIT_MS[spec.cls]
  let body: unknown
  try {
    const response =
      spec.method === 'GET'
        ? await webClient.get(url, { params: queryOf(given), timeout, signal })
        : await webClient.request({
            url,
            method: spec.method,
            data: bodyOf(given),
            timeout,
            signal,
          })
    if (sessionEnded(undefined, null, response.request?.responseURL)) {
      throw new JournalError({ code: 'unauthorized', message: WORDS.signedOut })
    }
    body = response.data
  } catch (error) {
    throw new JournalError(problemOf(error))
  }
  const answer = body as { status?: unknown; enabled?: unknown; data?: unknown; message?: unknown }
  if (answer && typeof answer === 'object' && answer.status === 'success') {
    if (answer.enabled === false) {
      return { enabled: false, data: null, message: text(answer.message) ?? WORDS.off }
    }
    if (answer.data !== undefined && answer.data !== null) {
      return { enabled: true, data: answer.data as JournalAnswers[N]['res'] }
    }
  }
  throw new JournalError(problemFrom(body) ?? { code: 'unreadable', message: WORDS.unreadable })
}

/** The data of an op, or a thrown sentence: for a read that cannot go on while the journal is off. */
export async function journalData<N extends Exclude<OpName, 'delivery' | 'family_day'>>(
  name: N,
  args?: JournalAnswers[N]['req'],
  signal?: AbortSignal
): Promise<JournalAnswers[N]['res']> {
  const result = await journalCall(name, args, signal)
  if (!result.enabled) throw new JournalError({ code: 'unavailable', message: result.message })
  return result.data
}

export type { Done }
