/**
 * The chart documents' bridge client (M4.18): the switch asked once and again after a while, the
 * envelope read for a document, a write with its CSRF token, a stale write with the newer copy,
 * every problem in words, and no answer read as offline. Nothing reaches a network: `fetch` is a
 * fake that answers by path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CHARTS_API, chartsApi, MAX_DOC_BYTES, overLimit } from './api'

type Answer = { status?: number; body?: unknown; text?: string } | Error

function fakeFetch(answers: Record<string, Answer | Answer[]>) {
  const calls: Array<{ path: string; init?: RequestInit }> = []
  const fn = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init })
    const route = Object.keys(answers).find((prefix) => path.startsWith(prefix))
    const listed = route === undefined ? undefined : answers[route]
    const answer = Array.isArray(listed) ? listed.shift() : listed
    if (!answer) throw new TypeError(`no answer for ${path}`)
    if (answer instanceof Error) throw answer
    const text = answer.text ?? JSON.stringify(answer.body ?? null)
    return new Response(text, { status: answer.status ?? 200 })
  })
  return { fn, calls }
}

const ON = { body: { status: 'ok', data: { enabled: true } } }
const OFF = { body: { status: 'ok', data: { enabled: false } } }
const TOKEN = { body: { csrf_token: 'tok' } }
const DOC = { kind: 'drawings', key: 'NSE:SBIN', revision: 1794000000000, updated_ms: 1 }

beforeEach(() => {
  vi.useFakeTimers({ now: Date.UTC(2026, 10, 15, 4) })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('the switch', () => {
  it('is asked once and the answer kept while it is on', async () => {
    const net = fakeFetch({ [`${CHARTS_API}/state`]: ON })
    const api = chartsApi(net.fn)
    await expect(api.enabled()).resolves.toBe(true)
    await expect(api.enabled()).resolves.toBe(true)
    expect(net.calls.map((c) => c.path)).toEqual([`${CHARTS_API}/state`])
  })

  it('read as off, or not read at all, is asked again only after five minutes', async () => {
    const net = fakeFetch({ [`${CHARTS_API}/state`]: [OFF, new TypeError('offline'), ON] })
    const api = chartsApi(net.fn)
    await expect(api.enabled()).resolves.toBe(false)
    await expect(api.enabled()).resolves.toBe(false)
    vi.advanceTimersByTime(5 * 60_000)
    await expect(api.enabled()).resolves.toBe(false) // no answer: off for now
    vi.advanceTimersByTime(5 * 60_000)
    await expect(api.enabled()).resolves.toBe(true)
    expect(net.calls).toHaveLength(3)
  })

  it('an answer saying off turns it off for the page, whatever the state said', async () => {
    const off = { status: 503, body: { status: 'error', code: 'off', message: 'Switched off.' } }
    const net = fakeFetch({ [`${CHARTS_API}/state`]: ON, [`${CHARTS_API}/doc`]: off })
    const api = chartsApi(net.fn)
    await api.enabled()
    const read = await api.read('drawings', 'NSE:SBIN')
    expect(read).toEqual({ ok: false, problem: { code: 'off', message: 'Switched off.' } })
    await expect(api.enabled()).resolves.toBe(false)
  })

  it('asks with the page’s own session and never a cached answer', async () => {
    const net = fakeFetch({ [`${CHARTS_API}/state`]: ON })
    await chartsApi(net.fn).enabled()
    expect(net.calls[0].init).toMatchObject({ credentials: 'same-origin', cache: 'no-store' })
  })
})

describe('a read', () => {
  it('answers the document, none, or the document without its body when it is held', async () => {
    const net = fakeFetch({
      [`${CHARTS_API}/doc`]: [
        { body: { status: 'ok', data: { doc: { ...DOC, body: { version: 2, drawings: [] } } } } },
        { body: { status: 'ok', data: { doc: null } } },
        { body: { status: 'ok', data: { doc: DOC } } },
      ],
    })
    const api = chartsApi(net.fn)
    expect(await api.read('drawings', 'NSE:SBIN')).toEqual({
      ok: true,
      doc: { ...DOC, body: { version: 2, drawings: [] } },
    })
    expect(await api.read('drawings', 'NSE:SBIN')).toEqual({ ok: true, doc: null })
    expect(await api.read('drawings', 'NSE:SBIN', DOC.revision)).toEqual({ ok: true, doc: DOC })
    expect(net.calls[2].path).toBe(
      `${CHARTS_API}/doc?kind=drawings&key=NSE%3ASBIN&have=${DOC.revision}`
    )
  })

  it('reads no answer, a page that is not the bridge’s, or a shape it does not know as a problem', async () => {
    const net = fakeFetch({
      [`${CHARTS_API}/doc`]: [
        new TypeError('Failed to fetch'),
        { status: 502, text: '<html>Bad gateway</html>' },
        { status: 200, body: { status: 'ok', data: { doc: { kind: 'drawings' } } } },
        { status: 404, text: 'Not found' },
      ],
    })
    const api = chartsApi(net.fn)
    const codes = []
    for (let i = 0; i < 4; i++) {
      const read = await api.read('drawings', 'NSE:SBIN')
      if (!read.ok) codes.push(read.problem.code)
    }
    expect(codes).toEqual(['offline', 'offline', 'unreadable', 'unreadable'])
  })

  it('gives up on a read the server does not answer within ten seconds', async () => {
    const fn = vi.fn(
      (_path: string, init?: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted')))
        })
    )
    const pending = chartsApi(fn).read('drawings', 'NSE:SBIN')
    await vi.advanceTimersByTimeAsync(10_000)
    await expect(pending).resolves.toMatchObject({ ok: false, problem: { code: 'offline' } })
  })
})

describe('a write', () => {
  it('carries the CSRF token, the revision it replaces and the body', async () => {
    const net = fakeFetch({
      '/auth/csrf-token': TOKEN,
      [`${CHARTS_API}/doc`]: { body: { status: 'ok', data: { doc: DOC } } },
    })
    const answer = await chartsApi(net.fn).write('drawings', 'NSE:SBIN', 7, { version: 2 })
    expect(answer).toEqual({ ok: true, doc: DOC })
    const put = net.calls[1]
    expect(put.init?.method).toBe('PUT')
    expect(put.init?.headers).toMatchObject({ 'X-CSRFToken': 'tok' })
    expect(JSON.parse(String(put.init?.body))).toEqual({
      kind: 'drawings',
      key: 'NSE:SBIN',
      base: 7,
      body: { version: 2 },
    })
  })

  it('refused as stale brings the newer document back, or none', async () => {
    const newer = { ...DOC, body: { version: 2, drawings: [{ id: 'x' }] } }
    const net = fakeFetch({
      '/auth/csrf-token': TOKEN,
      [`${CHARTS_API}/doc`]: [
        { status: 409, body: { status: 'error', code: 'stale', message: 'm', doc: newer } },
        { status: 409, body: { status: 'error', code: 'stale', message: 'm', doc: null } },
      ],
    })
    const api = chartsApi(net.fn)
    expect(await api.write('drawings', 'NSE:SBIN', 1, {})).toEqual({ ok: false, stale: newer })
    expect(await api.write('drawings', 'NSE:SBIN', 1, {})).toEqual({ ok: false, stale: null })
  })

  it('every refusal keeps the server’s own sentence, and a proxy’s page gets one of ours', async () => {
    const said = (code: string, status: number) => ({
      status,
      body: { status: 'error', code, message: `The server says ${code}.` },
    })
    const net = fakeFetch({
      '/auth/csrf-token': TOKEN,
      [`${CHARTS_API}/doc`]: [
        said('too_big', 413),
        said('limit', 422),
        said('session', 401),
        said('busy', 429),
        { status: 413, text: '<html>413 Request Entity Too Large</html>' },
      ],
    })
    const api = chartsApi(net.fn)
    const problems = []
    for (let i = 0; i < 5; i++) {
      const answer = await api.write('drawings', 'NSE:SBIN', 0, {})
      if (!answer.ok && 'problem' in answer) problems.push(answer.problem)
    }
    expect(problems.slice(0, 4)).toEqual([
      { code: 'too_big', message: 'The server says too_big.' },
      { code: 'limit', message: 'The server says limit.' },
      { code: 'session', message: 'The server says session.' },
      { code: 'busy', message: 'The server says busy.' },
    ])
    expect(problems[4].code).toBe('too_big')
    for (const problem of problems) expect(problem.message).not.toMatch(/\b4\d\d\b|html|HTTP/i)
  })

  it('without a CSRF token sends nothing', async () => {
    const net = fakeFetch({ '/auth/csrf-token': { body: {} } })
    const answer = await chartsApi(net.fn).write('drawings', 'NSE:SBIN', 0, {})
    expect(answer).toMatchObject({ ok: false, problem: { code: 'offline' } })
    expect(net.calls.map((c) => c.path)).toEqual(['/auth/csrf-token'])
  })
})

describe('the size of a document', () => {
  it('is counted in bytes, and only measured when it could be past the limit', () => {
    expect(overLimit('a'.repeat(MAX_DOC_BYTES))).toBe(false)
    expect(overLimit('a'.repeat(MAX_DOC_BYTES + 1))).toBe(true)
    expect(overLimit('é'.repeat(MAX_DOC_BYTES / 2 + 1))).toBe(true)
  })
})
