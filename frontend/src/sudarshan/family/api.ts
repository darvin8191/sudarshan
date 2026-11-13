// SUDARSHAN P6 D2: the one client for the family service's viewer routes (`/family/api/*`).
//
// Every route of the contract (types.ts, generated from sudarshan/family/contract.py) is one
// call: its method, its path and its answer's shape come from the contract's own table, so a
// route added there is a call here without another client. Differences from the owner's
// clients, on purpose:
//
//  - plain `fetch` with the session cookie. The owner's axios client asks `/auth/csrf-token`
//    first, which is an owner path a viewer phone must never reach;
//  - the `X-Family-CSRF` header on every non-GET, read from the page-readable
//    `__Host-sudarshan_family_csrf` cookie (the service compares it with the cookie);
//  - an error is the service's own sentence for a trader. A status, a code or an address is
//    never put in front of the viewer: when no sentence came (the phone is offline, the answer
//    is not the service's) a sentence of this file stands in.
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  type Envelope,
  type ErrorCode,
  VIEWER_OPS,
  VIEWER_PREFIX,
  type ViewerOpName,
  type ViewerOps,
} from './types'

/** What the phone says when no sentence came from the service. */
export const NO_ANSWER =
  'The family view cannot be reached right now. Check the connection, then try again.'
const UNREADABLE = 'The family view answered in a way this app cannot read. Try again in a minute.'
const NOT_SENT = 'That could not be sent. Reload the page, then try again.'

/** The wait for one call: a read of the portfolio can take a few seconds on the server. */
const WAIT_MS = 15_000

export type ProblemCode = ErrorCode | 'unanswered' | 'unreadable' | 'unsent'

/** An error in the viewer's words, with the code the screen acts on (never shown). */
export class FamilyError extends Error {
  readonly code: ProblemCode
  /** The input the sentence is about (a form field), when the service names one. */
  readonly field: string | null
  constructor(code: ProblemCode, message: string, field: string | null = null) {
    super(message)
    this.name = 'FamilyError'
    this.code = code
    this.field = field
  }
}

// ---- the signed-out announcement -------------------------------------------------------------
const signedOutListeners = new Set<() => void>()

/** A call that a session route answered "signed out" (or a socket closed 4401) tells the shell. */
export function onSignedOut(listener: () => void): () => void {
  signedOutListeners.add(listener)
  return () => signedOutListeners.delete(listener)
}

export function announceSignedOut(): void {
  for (const listener of [...signedOutListeners]) listener()
}

// ---- the call --------------------------------------------------------------------------------
const SPECS = new Map(VIEWER_OPS.map((spec) => [spec.name, spec]))

/** The CSRF cookie's value, or null where there is none (signed out, or cookies blocked). */
export function csrfToken(): string | null {
  try {
    for (const part of document.cookie.split(';')) {
      const [name, ...rest] = part.trim().split('=')
      if (name === CSRF_COOKIE) return decodeURIComponent(rest.join('='))
    }
  } catch {
    // no cookie access: the service refuses the call with its own sentence
  }
  return null
}

const PATH_PARAMS = ['code', 'id', 'part'] as const

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function pathOf(route: string, args: Record<string, unknown>): string {
  let path = route
  for (const name of PATH_PARAMS) {
    if (path.includes(`<${name}>`)) {
      path = path.replace(`<${name}>`, encodeURIComponent(String(args[name] ?? '')))
    }
  }
  return `${VIEWER_PREFIX}${path}`
}

function bodyOf(route: string, args: Record<string, unknown>): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(args)) {
    if (route.includes(`<${name}>`)) continue // already in the path
    if (value !== undefined) body[name] = value
  }
  return body
}

async function answerOf(response: Response): Promise<Envelope<unknown> | null> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'status' in body) return body as Envelope<unknown>
  } catch {
    // not JSON
  }
  return null
}

/** One viewer op. Resolves to its data, or throws a `FamilyError` in the viewer's words. */
export async function familyCall<N extends ViewerOpName>(
  name: N,
  args?: ViewerOps[N]['req'],
  signal?: AbortSignal
): Promise<ViewerOps[N]['res']> {
  const spec = SPECS.get(name)
  if (!spec) throw new FamilyError('unsent', NOT_SENT)
  const given = (args ?? {}) as Record<string, unknown>
  const headers: Record<string, string> = { Accept: 'application/json' }
  const init: RequestInit = { method: spec.method, credentials: 'same-origin', headers }
  if (spec.method !== 'GET') {
    const token = csrfToken()
    if (token) headers[CSRF_HEADER] = token
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(bodyOf(spec.route, given))
  }
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), WAIT_MS)
  signal?.addEventListener('abort', () => abort.abort(), { once: true })
  init.signal = abort.signal
  let response: Response
  try {
    response = await fetch(pathOf(spec.route, given), init)
  } catch {
    throw new FamilyError('unanswered', NO_ANSWER)
  } finally {
    clearTimeout(timer)
  }
  const answer = await answerOf(response)
  if (answer?.status === 'success') return answer.data as ViewerOps[N]['res']
  if (answer?.status === 'error') {
    const message = text(answer.message) ?? UNREADABLE
    // `me` is the shell's own check: it reads the answer itself, and announcing it would
    // make the shell check again, forever.
    if (answer.code === 'signed_out' && spec.auth === 'session' && name !== 'me') {
      announceSignedOut()
    }
    throw new FamilyError(answer.code, message, text(answer.field))
  }
  throw new FamilyError('unreadable', UNREADABLE)
}

// ---- one method per route --------------------------------------------------------------------
type Args<N extends ViewerOpName> = ViewerOps[N]['req']
type Res<N extends ViewerOpName> = Promise<ViewerOps[N]['res']>

/** Every viewer route, by the contract's name for it. */
export const familyApi = {
  // auth
  joinInfo: (code: string): Res<'join_info'> => familyCall('join_info', { code }),
  join: (body: Args<'join'>): Res<'join'> => familyCall('join', body),
  login: (body: Args<'login'>): Res<'login'> => familyCall('login', body),
  logout: (): Res<'logout'> => familyCall('logout'),
  logoutAll: (): Res<'logout_all'> => familyCall('logout_all'),
  me: (signal?: AbortSignal): Res<'me'> => familyCall('me', undefined, signal),
  password: (body: Args<'password'>): Res<'password'> => familyCall('password', body),
  devices: (): Res<'devices'> => familyCall('devices'),
  deviceRename: (id: string, label: string): Res<'device_rename'> =>
    familyCall('device_rename', { id, label }),
  deviceSignout: (id: string): Res<'device_signout'> => familyCall('device_signout', { id }),
  // market and books
  market: (signal?: AbortSignal): Res<'market'> => familyCall('market', undefined, signal),
  summary: (signal?: AbortSignal): Res<'summary'> => familyCall('summary', undefined, signal),
  book: (part: Args<'book'>['part'], signal?: AbortSignal): Res<'book'> =>
    familyCall('book', { part }, signal),
  // portfolio
  portfolio: (signal?: AbortSignal): Res<'portfolio'> => familyCall('portfolio', undefined, signal),
  portfolioTab: (id: number, signal?: AbortSignal): Res<'portfolio_tab'> =>
    familyCall('portfolio_tab', { id }, signal),
  // watchlists
  watchlists: (): Res<'watchlists'> => familyCall('watchlists'),
  watchlist: (id: number): Res<'watchlist'> => familyCall('watchlist', { id }),
  // settings, push and Telegram
  settings: (): Res<'settings'> => familyCall('settings'),
  settingsSet: (body: Args<'settings_set'>): Res<'settings_set'> =>
    familyCall('settings_set', body),
  pushKey: (): Res<'push_key'> => familyCall('push_key'),
  pushSubscribe: (body: Args<'push_subscribe'>): Res<'push_subscribe'> =>
    familyCall('push_subscribe', body),
  pushUnsubscribe: (endpoint: string): Res<'push_unsubscribe'> =>
    familyCall('push_unsubscribe', { endpoint }),
  testSend: (): Res<'test_send'> => familyCall('test_send'),
  telegramLink: (): Res<'telegram_link'> => familyCall('telegram_link'),
  telegramUnlink: (): Res<'telegram_unlink'> => familyCall('telegram_unlink'),
} as const
