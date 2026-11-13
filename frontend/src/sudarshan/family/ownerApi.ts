// SUDARSHAN P6 D7a: the one client for the owner's Family page. One call per admin op of the
// family contract (`AdminOps` in types.ts), sent to OpenAlgo's owner bridge
// (blueprints/sudarshan_family.py): `/sudarshan/family/api/<op>`, a GET with its fields in the
// query string for state, member_list, device_list, audit_list and share_list, a POST with a JSON
// body for every other op. The app's own client carries the session and the CSRF token.
//
// What an answer is: the contract's envelope. An error carries the service's own sentence for the
// owner, shown as it is; a status, a code or an address is never put on the page. Two codes are
// the bridge's: `unavailable` (nothing was sent) and `not_confirmed` (a write went out and was not
// answered: it may have been done, so this client says so and never says "failed").
// The family view switched off answers with the Off record as data, not as an error.
import axios from 'axios'
import { webClient } from '@/api/client'
import type { AdminOpName, AdminOps, ErrorCode, Off } from './types'

/** What the page says when no sentence came from the bridge. */
export const NO_ANSWER =
  'No answer came from OpenAlgo. Check the connection, then open this page again to see whether it was done.'
export const UNREADABLE = 'OpenAlgo answered in a way this page cannot read. Try again in a minute.'
export const NOT_SENT = 'That could not be sent. Reload the page, then try again.'
export const NOT_CONFIRMED = 'Not confirmed: open the Family page again to see whether it was done.'

export type OwnerProblemCode =
  | ErrorCode
  | 'unavailable'
  | 'not_confirmed'
  | 'unanswered'
  | 'unreadable'
  | 'unsent'

/** An error in the owner's words, with the code the page acts on (never shown). */
export class OwnerError extends Error {
  readonly code: OwnerProblemCode
  /** The input the sentence is about, when the bridge names one. */
  readonly field: string | null
  constructor(code: OwnerProblemCode, message: string, field: string | null = null) {
    super(message)
    this.name = 'OwnerError'
    this.code = code
    this.field = field
  }
  /** A write that may have happened: the page reads its state again and says "not confirmed". */
  get unconfirmed(): boolean {
    return this.code === 'not_confirmed' || this.code === 'unanswered'
  }
}

/** What an op returned: its data, or that the family view is switched off (with its sentence). */
export type OwnerResult<T> =
  | { enabled: true; data: T }
  | { enabled: false; data: null; message: string }

export const API_PREFIX = '/sudarshan/family/api'
/** The bridge's own waits are 3 s for a read and 6 s for a write; this allows the trip. */
const WAIT_MS = { read: 12_000, write: 15_000 } as const
const READS = new Set<AdminOpName>([
  'state',
  'member_list',
  'device_list',
  'audit_list',
  'share_list',
])

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function isOff(data: unknown): data is Off {
  return (
    !!data &&
    typeof data === 'object' &&
    (data as Off).enabled === false &&
    typeof (data as Off).message === 'string'
  )
}

function problemFrom(body: unknown): OwnerError | null {
  if (!body || typeof body !== 'object') return null
  const { status, code, message, field } = body as Record<string, unknown>
  if (status !== 'error' || typeof code !== 'string') return null
  const sentence = text(message)
  if (!sentence) return null
  return new OwnerError(code as OwnerProblemCode, sentence, text(field))
}

function queryOf(args: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, value] of Object.entries(args)) {
    if (value === undefined || value === null || value === '') continue
    out[name] = String(value)
  }
  return out
}

/** The problem in an error a request threw, in the owner's words. */
export function ownerProblem(error: unknown): OwnerError {
  if (error instanceof OwnerError) return error
  if (!axios.isAxiosError(error)) return new OwnerError('unsent', NOT_SENT)
  if (!error.response) return new OwnerError('unanswered', NO_ANSWER)
  return problemFrom(error.response.data) ?? new OwnerError('unreadable', UNREADABLE)
}

/** One admin op at the bridge. Resolves to its data, or throws an `OwnerError`. */
export async function ownerCall<N extends AdminOpName>(
  name: N,
  args?: AdminOps[N]['req'],
  signal?: AbortSignal
): Promise<OwnerResult<AdminOps[N]['res']>> {
  const given = (args ?? {}) as Record<string, unknown>
  const url = `${API_PREFIX}/${name}`
  const read = READS.has(name)
  let body: unknown
  try {
    const response = read
      ? await webClient.get(url, { params: queryOf(given), timeout: WAIT_MS.read, signal })
      : await webClient.post(url, given, { timeout: WAIT_MS.write, signal })
    body = response.data
  } catch (error) {
    throw ownerProblem(error)
  }
  const answer = body as { status?: unknown; data?: unknown }
  if (answer && typeof answer === 'object' && answer.status === 'success') {
    // `state` carries its own `enabled`: it is an answer, not the Off record.
    if (name !== 'state' && isOff(answer.data)) {
      return { enabled: false, data: null, message: answer.data.message }
    }
    if (answer.data !== undefined && answer.data !== null) {
      return { enabled: true, data: answer.data as AdminOps[N]['res'] }
    }
  }
  throw problemFrom(body) ?? new OwnerError('unreadable', UNREADABLE)
}

/** The data of an op, or an `OwnerError` that says the family view is switched off. */
export async function ownerData<N extends AdminOpName>(
  name: N,
  args?: AdminOps[N]['req'],
  signal?: AbortSignal
): Promise<AdminOps[N]['res']> {
  const result = await ownerCall(name, args, signal)
  if (!result.enabled) throw new OwnerError('off', result.message)
  return result.data
}

/** A fresh id for one attempt at a write that takes a `request_id`. */
export function newRequestId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
  }
}

type Args<N extends AdminOpName> = AdminOps[N]['req']

/** Every admin route, by the contract's name for it. */
export const ownerApi = {
  state: (signal?: AbortSignal) => ownerCall('state', undefined, signal),
  memberList: (signal?: AbortSignal) => ownerData('member_list', undefined, signal),
  memberCreate: (body: Args<'member_create'>) => ownerData('member_create', body),
  memberUpdate: (body: Args<'member_update'>) => ownerData('member_update', body),
  memberStatus: (body: Args<'member_status'>) => ownerData('member_status', body),
  memberRemove: (body: Args<'member_remove'>) => ownerData('member_remove', body),
  inviteCreate: (body: Args<'invite_create'>) => ownerData('invite_create', body),
  inviteRevoke: (body: Args<'invite_revoke'>) => ownerData('invite_revoke', body),
  signinReset: (body: Args<'signin_reset'>) => ownerData('signin_reset', body),
  deviceList: (memberId: number, signal?: AbortSignal) =>
    ownerData('device_list', { member_id: memberId }, signal),
  deviceRevoke: (body: Args<'device_revoke'>) => ownerData('device_revoke', body),
  auditList: (body: Args<'audit_list'>, signal?: AbortSignal) =>
    ownerData('audit_list', body, signal),
  shareList: (signal?: AbortSignal) => ownerData('share_list', undefined, signal),
  shareSet: (body: Args<'share_set'>) => ownerData('share_set', body),
  shareRemove: (body: Args<'share_remove'>) => ownerData('share_remove', body),
} as const
