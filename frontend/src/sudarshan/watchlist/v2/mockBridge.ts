import { AxiosError, type AxiosResponse } from 'axios'
import { API_PREFIX, type Envelope, OPS, type OpName } from './types'

/**
 * A stand-in for the owner's bridge, for tests of the v2 client: it answers the contract's routes
 * from handlers a test sets per op, and records every call it was made (op, path, the query or the
 * body). Used as `vi.mock('@/api/client', () => ({ webClient: bridge.client }))`.
 */
export interface Call {
  op: OpName
  method: string
  path: string
  params: Record<string, unknown>
  body: unknown
}

type Handler = (call: Call) => unknown | Promise<unknown>

export function createBridge() {
  const handlers = new Map<OpName, Handler>()
  const calls: Call[] = []
  /** Every request the page asked of the client, a route the bridge does not know included. */
  const requests: Array<{ method: string; path: string }> = []

  const match = (method: string, path: string): OpName | null => {
    const bare = path.slice(API_PREFIX.length)
    for (const spec of OPS) {
      if (spec.method !== method) continue
      const pattern = new RegExp(`^${spec.route.replace(/<\w+>/g, '[^/]+')}$`)
      if (pattern.test(bare)) return spec.name
    }
    return null
  }

  const answer = (status: number, data: unknown) =>
    ({ status, data, statusText: '', headers: {}, config: {} }) as AxiosResponse

  /** What the real bridge answers for a request it cannot read (`_Bad`): `invalid`, a field named. */
  const invalid = (field: string) =>
    new AxiosError(
      'refused',
      'ERR_BAD_REQUEST',
      undefined,
      undefined,
      answer(400, {
        status: 'error',
        code: 'invalid',
        message: 'SUDARSHAN could not understand that change.',
        field,
      })
    )

  const send = async (method: string, path: string, params: object, body: unknown) => {
    requests.push({ method, path })
    const op = match(method, path)
    if (!op) throw new Error(`no such route: ${method} ${path}`)
    // The bridge reads a GET or DELETE from the query string (primitives only) and every other
    // method from the JSON body (`_read`); a field in the other place is not seen, so a request
    // built that way is refused here too instead of passing against a shape the bridge never reads.
    const queried = method === 'GET' || method === 'DELETE'
    if (queried && body !== undefined) throw invalid('body')
    if (!queried && Object.keys(params).length > 0) throw invalid('query')
    for (const [name, value] of Object.entries(params)) {
      if (value !== null && typeof value === 'object') throw invalid(name)
    }
    // On the wire a query value is text, and an absent or null one is not sent (axios).
    const wire = Object.fromEntries(
      Object.entries(params)
        .filter(([, value]) => value !== null && value !== undefined)
        .map(([name, value]) => [name, String(value)])
    )
    const call = { op, method, path, params: wire as Record<string, unknown>, body }
    calls.push(call)
    const handler = handlers.get(op)
    if (!handler) throw new Error(`no handler for ${op}`)
    const result = await handler(call)
    const envelope = result as Envelope<unknown> | undefined
    if (envelope && typeof envelope === 'object' && 'status' in envelope) {
      if (envelope.status === 'ok') return answer(200, envelope)
      throw new AxiosError(
        'refused',
        'ERR_BAD_REQUEST',
        undefined,
        undefined,
        answer(400, envelope)
      )
    }
    return answer(200, { status: 'ok', data: result })
  }

  const client = {
    get: (path: string, config: { params?: object } = {}) =>
      send('GET', path, config.params ?? {}, undefined),
    request: (config: { method: string; url: string; data?: unknown; params?: object }) =>
      send(config.method, config.url, config.params ?? {}, config.data),
  }

  return {
    client,
    calls,
    requests,
    /** What an op answers: its data, or a ready envelope (an error one is thrown as the client sees it). */
    on: (op: OpName, handler: Handler) => handlers.set(op, handler),
    count: (op: OpName) => calls.filter((call) => call.op === op).length,
    reset: () => {
      handlers.clear()
      calls.length = 0
      requests.length = 0
    },
  }
}
