// Synthetic answers for the family screens' tests. Nothing here is a real account, a real
// member or a real price: the names are the platform's own words and the figures are round.
import { vi } from 'vitest'
import type { Book, Me, Privacy } from '../types'

export const VIEWER_PRIVACY: Privacy = { pnl: true, qty: true, funds: false, orders: true }
export const CHILD_PRIVACY: Privacy = { pnl: false, qty: false, funds: false, orders: false }

export function meOf(kind: 'viewer' | 'child' = 'viewer', over: Partial<Me> = {}): Me {
  const child = kind === 'child'
  const privacy = child ? CHILD_PRIVACY : VIEWER_PRIVACY
  return {
    member: {
      id: 1,
      handle: 'member_one',
      display_name: 'Test Member',
      role: kind,
      language: 'en',
    },
    privacy,
    hidden: (Object.keys(privacy) as Array<keyof Privacy>).filter((key) => !privacy[key]),
    limited: child,
    device: {
      id: 'device-1',
      label: 'Android 15 · Chrome 130',
      created_at: 1,
      last_seen_at: 2,
      place: null,
      current: true,
      notifications: null,
    },
    expires_at: 9_999_999_999_999,
    ...over,
  }
}

export const ok = <T>(data: T) => ({ status: 'success' as const, data })

export const fail = (code: string, message: string, field?: string) => ({
  status: 'error' as const,
  code,
  message,
  ...(field ? { field } : {}),
})

type Reply = unknown | ((url: string, init?: RequestInit) => unknown)

export interface Router {
  calls: Array<{ url: string; method: string; headers: Record<string, string>; body: unknown }>
  urls: () => string[]
}

/**
 * A fetch that answers from a table of path prefixes (longest first). Every call is recorded,
 * so a test can assert what was asked and, as importantly, what was not. A path with no answer
 * is a failure the test sees, not a silent success.
 */
export function routeFetch(
  table: Record<string, Reply>,
  status: Record<string, number> = {}
): Router {
  const router: Router = { calls: [], urls: () => router.calls.map((call) => call.url) }
  const prefixes = Object.keys(table).sort((a, b) => b.length - a.length)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
      const headers = (init?.headers ?? {}) as Record<string, string>
      router.calls.push({
        url,
        method: init?.method ?? 'GET',
        headers,
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
      })
      const hit = prefixes.find((prefix) => url === prefix || url.startsWith(prefix))
      if (!hit)
        return new Response(JSON.stringify(fail('not_found', 'No such page.')), { status: 404 })
      const reply = table[hit]
      const body =
        typeof reply === 'function'
          ? (reply as (url: string, init?: RequestInit) => unknown)(url, init)
          : reply
      return new Response(JSON.stringify(body), { status: status[hit] ?? 200 })
    })
  )
  return router
}

/** A book answer around some rows. */
export function bookOf(part: Book['part'], rows: unknown[], over: Partial<Book> = {}): Book {
  return {
    part,
    read_at: Date.now() - 5_000,
    state: 'ok',
    words: null,
    hidden: [],
    rows,
    ...over,
  }
}

/** A FakeSocket the tests drive by hand. */
export class FakeSocket {
  static instances: FakeSocket[] = []
  static OPEN = 1
  static CONNECTING = 0
  static CLOSING = 2
  static CLOSED = 3
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number; wasClean: boolean }) => void) | null = null
  onerror: (() => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close(code = 1000) {
    this.readyState = 3
    this.onclose?.({ code, wasClean: code === 1000 })
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
  say(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
  static reset() {
    FakeSocket.instances = []
  }
  static last(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1]
  }
}
