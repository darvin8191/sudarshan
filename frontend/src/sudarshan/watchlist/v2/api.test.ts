import { AxiosError } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

import { callOp, routeOf, WatchlistsError } from './api'
import { OPS } from './types'

const b = () => bridge.current as NonNullable<typeof bridge.current>

beforeEach(() => b().reset())

describe('routes', () => {
  it('puts the id of a path into it and keeps the rest for the body or the query', () => {
    expect(routeOf('section_edit', { id: 4, name: 'x' }).path).toBe(
      '/sudarshan/watchlists/api/sections/4'
    )
    expect(routeOf('arrange', { id: 2 }).path).toBe('/sudarshan/watchlists/api/lists/2/arrange')
    expect(routeOf('mark', { k: 'NSE:M&M' }).path).toBe(
      '/sudarshan/watchlists/api/symbols/NSE%3AM%26M'
    )
    expect(routeOf('view', { ref: 'list:1' }).path).toBe('/sudarshan/watchlists/api/view')
  })

  it('refuses a request that lacks the field its path needs, in words', () => {
    expect(() => routeOf('section_remove', {})).toThrow(WatchlistsError)
  })
})

describe('every op, in the shape the bridge reads', () => {
  // contract.py: a GET or DELETE reads its fields from the query string, every other method from
  // the JSON body. The mock bridge refuses the other place, so a request built wrongly fails here
  // as it would against the real bridge.
  it.each(OPS.map((spec) => [spec.name, spec.method] as const))('%s (%s)', async (name, method) => {
    b().on(name, () => ({}))
    const req = { id: 3, ref: 'list:1', version: '5', list: 1, k: 'NSE:INFY' }
    await callOp(name, req as never)
    const [call] = b().calls
    if (method === 'GET' || method === 'DELETE') {
      expect(call.body).toBeUndefined()
      expect(call.params.version).toBe('5')
    } else {
      expect(call.params).toEqual({})
      expect(call.body).toMatchObject({ version: '5' })
    }
  })

  it('sends a DELETE field in the query, never in the body', async () => {
    b().on('section_remove', () => ({ version: '6', id: 7, words: null }))
    await callOp('section_remove', { id: 7, version: '3' })
    expect(b().calls[0]).toMatchObject({
      method: 'DELETE',
      path: '/sudarshan/watchlists/api/sections/7',
      params: { version: '3' },
    })
    expect(b().calls[0].body).toBeUndefined()
  })
})

describe('callOp', () => {
  it('reads with the query, not the path fields', async () => {
    b().on('view', () => ({ ref: 'list:1', version: 'a:1' }))
    expect(await callOp('view', { ref: 'list:1' })).toMatchObject({ version: 'a:1' })
    expect(b().calls[0]).toMatchObject({ method: 'GET', params: { ref: 'list:1' } })
  })

  it('writes with the whole request as the body', async () => {
    b().on('section_edit', () => ({ version: 'a:2', id: 4, words: null }))
    await callOp('section_edit', { id: 4, name: 'Swing', version: 'a:1' })
    expect(b().calls[0]).toMatchObject({
      method: 'PATCH',
      path: '/sudarshan/watchlists/api/sections/4',
      body: { id: 4, name: 'Swing', version: 'a:1' },
    })
  })

  it('throws the server words and the field of a refusal', async () => {
    b().on('section_add', () => ({
      status: 'error',
      code: 'invalid',
      message: 'A section needs a name.',
      field: 'name',
    }))
    const error = await callOp('section_add', { list: 1, name: '', version: 'a:1' }).catch((e) => e)
    expect(error).toBeInstanceOf(WatchlistsError)
    expect(error).toMatchObject({
      code: 'invalid',
      message: 'A section needs a name.',
      field: 'name',
    })
  })

  it('speaks for a code that came without a sentence, never with a status or an address', async () => {
    b().on('state', () => ({ status: 'error', code: 'off', message: '' }))
    const error = await callOp('state', {}).catch((e) => e)
    expect(error.code).toBe('off')
    expect(error.message).toMatch(/switched off/)
    expect(error.message).not.toMatch(/\d{3}|\/sudarshan|HTTP/)
  })

  // Review L14: `limit` is a cap (the rate limit is `busy`), said as the server says it.
  it('speaks for a limit that came without a sentence as the cap it is, not as a rate', async () => {
    b().on('state', () => ({ status: 'error', code: 'limit', message: '' }))
    const error = await callOp('state', {}).catch((e) => e)
    expect(error.code).toBe('limit')
    expect(error.message).toBe('That is more than a list can hold.')
  })

  it('says so when no answer came or the answer is not the bridge', async () => {
    b().on('state', () => {
      throw new AxiosError('Network Error', 'ERR_NETWORK')
    })
    expect(await callOp('state', {}).catch((e) => e)).toMatchObject({ code: 'unanswered' })
    const html = b().client
    vi.spyOn(html, 'request').mockResolvedValueOnce({ data: '<html>' } as never)
    expect(await callOp('state', {}).catch((e) => e)).toMatchObject({ code: 'unreadable' })
  })
})
