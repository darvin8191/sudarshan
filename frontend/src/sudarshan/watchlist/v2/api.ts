import axios from 'axios'
import { webClient } from '@/api/client'
import {
  API_PREFIX,
  type Cls,
  type Envelope,
  type ErrorCode,
  OPS,
  type OpName,
  type WatchlistOps,
} from './types'

/**
 * The v2 watchlist bridge (`blueprints/sudarshan_watchlists.py`), reached through the routes the
 * contract names (`types.ts`, generated: never edited here). Every answer is the envelope: on an
 * error its `message` is already in the owner's words and is shown as it is; `code` is what the
 * page acts on. A problem with no envelope (no answer, a page that is not the bridge's) gets
 * words of the page's own, never a status code or an address.
 */
export type ProblemCode = ErrorCode | 'unanswered' | 'unsent' | 'unreadable'

const NO_ANSWER = 'No answer came from OpenAlgo. Check the connection, then try again.'
const NOT_SENT = 'The request could not be sent. Reload the page, then try again.'
const UNREADABLE = 'OpenAlgo’s answer could not be read. Try again in a minute.'

/** What to say when the bridge named a code but gave no sentence. */
const BY_CODE: Partial<Record<ProblemCode, string>> = {
  off: 'The new watchlist features are switched off on this server.',
  session: 'You are signed out. Sign in again, then try again.',
  owner_only: 'Only the owner of this server can change watchlists.',
  limit: 'Too many requests just now. Wait a moment, then try again.',
  busy: 'The watchlists are busy. Try again in a moment.',
  conflict: 'The list changed on another screen. It has been read again; try once more.',
  gone: 'That list or symbol is no longer there.',
  unanswered: NO_ANSWER,
  unsent: NOT_SENT,
  unreadable: UNREADABLE,
}

export class WatchlistsError extends Error {
  readonly code: ProblemCode
  readonly field: string | null

  constructor(code: ProblemCode, message: string, field: string | null = null) {
    super(message)
    this.name = 'WatchlistsError'
    this.code = code
    this.field = field
  }
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function isEnvelope(body: unknown): body is Envelope<unknown> {
  const status = (body as { status?: unknown } | null)?.status
  return status === 'ok' || status === 'error'
}

/** The problem in what a request threw or answered, in the owner's words. */
export function problemOf(error: unknown): WatchlistsError {
  if (error instanceof WatchlistsError) return error
  if (!axios.isAxiosError(error)) return new WatchlistsError('unsent', NOT_SENT)
  if (!error.response) return new WatchlistsError('unanswered', NO_ANSWER)
  return refusal(error.response.data)
}

/** The problem an answer's body states; an answer that is not the bridge's is unreadable. */
function refusal(body: unknown): WatchlistsError {
  if (!isEnvelope(body) || body.status !== 'error') {
    return new WatchlistsError('unreadable', UNREADABLE)
  }
  const code = (text(body.code) ?? 'failed') as ProblemCode
  return new WatchlistsError(
    code,
    text(body.message) ?? BY_CODE[code] ?? UNREADABLE,
    text(body.field)
  )
}

/** How long each class of op is waited for, in ms (the bridge itself gives up sooner). */
const WAIT: Record<Cls, number> = {
  read: 15_000,
  write: 20_000,
  search: 8_000,
  ranked: 15_000,
  preview: 15_000,
}

/** The methods whose fields travel in the query string (contract.py: primitives only). */
const QUERY_METHODS = new Set<string>(['GET', 'DELETE'])

const SPECS = new Map(OPS.map((spec) => [spec.name, spec]))

/** The path an op's request is made on, and the fields the path took (`/sections/<id>`). */
export function routeOf(op: OpName, req: object): { path: string; used: Set<string> } {
  const spec = SPECS.get(op)
  if (!spec) throw new WatchlistsError('unsent', NOT_SENT)
  const used = new Set<string>()
  const path = spec.route.replace(/<(\w+)>/g, (_, name: string) => {
    const value = (req as Record<string, unknown>)[name]
    if (value === undefined || value === null) throw new WatchlistsError('unsent', NOT_SENT)
    used.add(name)
    return encodeURIComponent(String(value))
  })
  return { path: `${API_PREFIX}${path}`, used }
}

/** One op of the bridge. Throws a `WatchlistsError` with the server's words. */
export async function callOp<N extends OpName>(
  op: N,
  req: WatchlistOps[N]['req'],
  signal?: AbortSignal
): Promise<WatchlistOps[N]['res']> {
  const spec = SPECS.get(op)
  if (!spec) throw new WatchlistsError('unsent', NOT_SENT)
  let body: unknown
  try {
    const { path, used } = routeOf(op, req)
    const fields = Object.fromEntries(Object.entries(req).filter(([name]) => !used.has(name)))
    const timeout = WAIT[spec.cls]
    // The contract's rule, which the bridge reads the same way: a GET or DELETE carries its
    // fields in the query string, every other method carries the whole request as a JSON body.
    const answer = QUERY_METHODS.has(spec.method)
      ? await webClient.request({ method: spec.method, url: path, params: fields, timeout, signal })
      : await webClient.request({ method: spec.method, url: path, data: req, timeout, signal })
    body = answer.data
  } catch (error) {
    if (axios.isCancel(error)) throw error
    throw problemOf(error)
  }
  if (isEnvelope(body) && body.status === 'ok' && body.data !== undefined) {
    return body.data as WatchlistOps[N]['res']
  }
  throw refusal(body)
}
