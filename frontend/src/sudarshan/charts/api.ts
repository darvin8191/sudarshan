/**
 * The chart documents' bridge (`blueprints/sudarshan_charts.py`, M4.18): whether the server keeps
 * the owner's chart documents, one document's read, and a write that names the revision it
 * replaces. Every answer is the bridge's envelope; a problem comes back as a code the device acts
 * on and the server's own sentence, or one of these when there was no answer, never a status
 * code or an address.
 *
 * Plain `fetch`, not the app's web client: that one sends the page to the sign-in screen on a
 * refusal, and a save in the background must never move the page. Whatever this answers, the
 * device keeps its own copy. Nothing here imports the chart engine.
 */

export const CHARTS_API = '/sudarshan/charts/api'

export type DocKind = 'drawings' | 'phone_set' | 'workspace' | 'template'

export interface ServerDoc {
  kind: DocKind
  key: string
  revision: number
  updated_ms: number
  /** Left out when the read named the revision it already holds. */
  body?: unknown
}

export type ProblemCode =
  | 'invalid'
  | 'session'
  | 'owner_only'
  | 'too_big'
  | 'limit'
  | 'busy'
  | 'failed'
  | 'off'
  | 'record'
  | 'offline'
  | 'unreadable'

export interface Problem {
  code: ProblemCode
  message: string
}

export type ReadResult = { ok: true; doc: ServerDoc | null } | { ok: false; problem: Problem }
export type WriteResult =
  | { ok: true; doc: ServerDoc }
  | { ok: false; stale: ServerDoc | null }
  | { ok: false; problem: Problem }

/** The engine's limit on one document, which the bridge holds too. */
export const MAX_DOC_BYTES = 5 * 1024 * 1024

/** Whether a document's text is past the limit in UTF-8 bytes; measured only when it could be. */
export function overLimit(text: string): boolean {
  if (text.length * 3 <= MAX_DOC_BYTES) return false
  return new TextEncoder().encode(text).length > MAX_DOC_BYTES
}

const OFFLINE = 'No answer came from the server, so this device keeps its own copy for now.'
const UNREADABLE = 'The server’s answer could not be read, so this device keeps its own copy.'
const TOO_BIG = 'That is larger than the server takes, so it is kept on this device only.'
const CODES = new Set<string>([
  'invalid',
  'session',
  'owner_only',
  'too_big',
  'limit',
  'busy',
  'failed',
  'off',
  'record',
])

/** How long an answer is waited for, in ms: a 5 MB layout on a slow line takes a while. */
const READ_WAIT = 10_000
const WRITE_WAIT = 30_000
/** A switch read as off (or not read) is asked again after this long, on the next save or read. */
const ASK_AGAIN_MS = 5 * 60_000

export interface ChartsApi {
  /** Whether the server keeps chart documents now: false when it is switched off or not known. */
  enabled(): Promise<boolean>
  read(kind: DocKind, key: string, have?: number): Promise<ReadResult>
  write(kind: DocKind, key: string, base: number, body: unknown): Promise<WriteResult>
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

function isDoc(value: unknown): value is ServerDoc {
  const doc = value as Partial<ServerDoc> | null
  return (
    !!doc &&
    typeof doc === 'object' &&
    typeof doc.kind === 'string' &&
    typeof doc.key === 'string' &&
    Number.isSafeInteger(doc.revision) &&
    (doc.revision as number) >= 1 &&
    Number.isSafeInteger(doc.updated_ms)
  )
}

/** The problem an answer states, or ours when it is not the bridge's envelope. */
function problemOf(status: number, body: unknown): Problem {
  const answer = body as { status?: unknown; code?: unknown; message?: unknown } | null
  if (answer?.status === 'error' && typeof answer.code === 'string' && CODES.has(answer.code)) {
    const message = typeof answer.message === 'string' && answer.message.trim()
    return { code: answer.code as ProblemCode, message: message || UNREADABLE }
  }
  if (status === 413) return { code: 'too_big', message: TOO_BIG } // a proxy in front refused it
  if (status >= 500 || status === 0) return { code: 'offline', message: OFFLINE }
  return { code: 'unreadable', message: UNREADABLE }
}

export function chartsApi(fetchImpl: Fetch = (input, init) => fetch(input, init)): ChartsApi {
  /** The switch as last read, and when; null before it is read. */
  let known: { on: boolean; at: number } | null = null
  let asking: Promise<boolean> | null = null

  async function send(
    path: string,
    init: RequestInit,
    wait: number
  ): Promise<{ status: number; body: unknown }> {
    const stop = new AbortController()
    const timer = setTimeout(() => stop.abort(), wait)
    try {
      const response = await fetchImpl(path, {
        credentials: 'same-origin',
        cache: 'no-store',
        ...init,
        signal: stop.signal,
      })
      let body: unknown = null
      try {
        body = await response.json()
      } catch {
        /* not the bridge's envelope: told by the status */
      }
      return { status: response.status, body }
    } finally {
      clearTimeout(timer)
    }
  }

  /** The answer's code was `off`: the switch is off now, whatever the state said. */
  const noteOff = (problem: Problem) => {
    if (problem.code === 'off') known = { on: false, at: Date.now() }
    return problem
  }

  async function ask(): Promise<boolean> {
    try {
      const { body } = await send(`${CHARTS_API}/state`, { method: 'GET' }, READ_WAIT)
      const data = (body as { status?: unknown; data?: { enabled?: unknown } } | null) ?? null
      const on = data?.status === 'ok' && data.data?.enabled === true
      known = { on, at: Date.now() }
      return on
    } catch {
      known = { on: false, at: Date.now() }
      return false
    }
  }

  return {
    async enabled() {
      if (known && (known.on || Date.now() - known.at < ASK_AGAIN_MS)) return known.on
      asking ??= ask().finally(() => {
        asking = null
      })
      return asking
    },

    async read(kind, key, have) {
      const query = new URLSearchParams({ kind, key })
      if (have !== undefined) query.set('have', String(have))
      try {
        const { status, body } = await send(
          `${CHARTS_API}/doc?${query}`,
          { method: 'GET' },
          READ_WAIT
        )
        const data = (body as { status?: unknown; data?: { doc?: unknown } } | null) ?? null
        if (status === 200 && data?.status === 'ok' && data.data && 'doc' in data.data) {
          const doc = data.data.doc
          if (doc === null) return { ok: true, doc: null }
          if (isDoc(doc)) return { ok: true, doc }
        }
        return { ok: false, problem: noteOff(problemOf(status, body)) }
      } catch {
        return { ok: false, problem: { code: 'offline', message: OFFLINE } }
      }
    },

    async write(kind, key, base, body) {
      try {
        const token = await send('/auth/csrf-token', { method: 'GET' }, READ_WAIT)
        const csrf = (token.body as { csrf_token?: unknown } | null)?.csrf_token
        if (typeof csrf !== 'string' || !csrf) return { ok: false, problem: problemOf(0, null) }
        const answer = await send(
          `${CHARTS_API}/doc`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({ kind, key, base, body }),
          },
          WRITE_WAIT
        )
        const data = answer.body as {
          status?: unknown
          code?: unknown
          data?: { doc?: unknown }
          doc?: unknown
        } | null
        if (answer.status === 200 && data?.status === 'ok' && isDoc(data.data?.doc))
          return { ok: true, doc: data.data.doc }
        if (data?.status === 'error' && data.code === 'stale')
          return { ok: false, stale: isDoc(data.doc) ? data.doc : null }
        return { ok: false, problem: noteOff(problemOf(answer.status, answer.body)) }
      } catch {
        return { ok: false, problem: { code: 'offline', message: OFFLINE } }
      }
    },
  }
}

/** The one client the page's screens share, so the switch is asked once for all of them. */
export const sharedChartsApi: ChartsApi = chartsApi()
