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

  it('says so when no answer came or the answer is not the bridge', async () => {
    b().on('state', () => {
      throw new AxiosError('Network Error', 'ERR_NETWORK')
    })
    expect(await callOp('state', {}).catch((e) => e)).toMatchObject({ code: 'unanswered' })
    const html = b().client
    vi.spyOn(html, 'get').mockResolvedValueOnce({ data: '<html>' } as never)
    expect(await callOp('state', {}).catch((e) => e)).toMatchObject({ code: 'unreadable' })
  })
})
