import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  announceSignedOut,
  FamilyError,
  familyApi,
  familyCall,
  NO_ANSWER,
  onSignedOut,
} from './api'
import { fail, ok, routeFetch } from './testing/fixtures'
import { CSRF_COOKIE, CSRF_HEADER, ERROR_STATUS, VIEWER_OPS } from './types'

// A __Host- cookie needs a secure page, which the test page is not: the cookie string is stubbed.
let jar = ''
function setCookie(value: string | null) {
  jar = value === null ? 'other=1' : `other=1; ${CSRF_COOKIE}=${value}`
}

beforeEach(() => {
  vi.spyOn(document, 'cookie', 'get').mockImplementation(() => jar)
  setCookie('csrf-value-1')
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('every viewer route of the contract has a call', () => {
  const ARGS: Record<string, Record<string, unknown>> = {
    join_info: { code: 'abc' },
    join: { code: 'abc', handle: 'h', password: 'p', totp: '123456' },
    login: { handle: 'h', password: 'p', totp: '123456' },
    password: { current: 'a', password: 'b', totp: '123456' },
    device_rename: { id: 'd1', label: 'Phone' },
    device_signout: { id: 'd1' },
    book: { part: 'positions' },
    portfolio_tab: { id: 7 },
    watchlist: { id: 3 },
    settings_set: { version: 1, language: 'en' },
    push_subscribe: { endpoint: 'https://push.example/x', keys: { p256dh: 'a', auth: 'b' } },
    push_unsubscribe: { endpoint: 'https://push.example/x' },
  }
  it.each(VIEWER_OPS.map((spec) => [spec.name, spec]))('%s', async (_name, spec) => {
    const router = routeFetch({ '/family/api': ok({}) })
    await familyCall(spec.name, (ARGS[spec.name] ?? {}) as never)
    const call = router.calls[0]
    expect(call.method).toBe(spec.method)
    expect(call.url.startsWith('/family/api/')).toBe(true)
    expect(call.url).not.toMatch(/[<>]/)
    // The CSRF header on every non-GET, and never on a GET.
    if (spec.method === 'GET') expect(call.headers[CSRF_HEADER]).toBeUndefined()
    else expect(call.headers[CSRF_HEADER]).toBe('csrf-value-1')
  })

  it('reaches no path outside /family/api', async () => {
    const router = routeFetch({ '/family/api': ok({}) })
    for (const spec of VIEWER_OPS) {
      await familyCall(spec.name, {
        code: 'a',
        id: 1,
        part: 'orders',
        handle: 'h',
        password: 'p',
        totp: '1',
        current: 'c',
        label: 'l',
        version: 1,
        endpoint: 'e',
        keys: { p256dh: 'a', auth: 'b' },
      } as never)
    }
    expect(router.urls().every((url) => url.startsWith('/family/api/'))).toBe(true)
    expect(router.urls()).toHaveLength(VIEWER_OPS.length)
  })

  it('has a named method for each route', () => {
    expect(Object.keys(familyApi)).toHaveLength(VIEWER_OPS.length)
  })
})

describe('what a call sends', () => {
  it('puts a path value in the path and out of the body', async () => {
    const router = routeFetch({ '/family/api': ok({}) })
    await familyApi.deviceRename('device 1', 'My phone')
    expect(router.calls[0].url).toBe('/family/api/devices/device%201')
    expect(router.calls[0].method).toBe('PATCH')
    expect(router.calls[0].body).toEqual({ label: 'My phone' })
  })

  it('sends the join code in the body of the join itself', async () => {
    const router = routeFetch({ '/family/api': ok({}) })
    await familyApi.join({ code: 'abc', handle: 'h', password: 'p', totp: '123456' })
    expect(router.calls[0].body).toEqual({
      code: 'abc',
      handle: 'h',
      password: 'p',
      totp: '123456',
    })
  })

  it('reads the book part and the tab id from the path', async () => {
    const router = routeFetch({ '/family/api': ok({}) })
    await familyApi.book('holdings')
    await familyApi.portfolioTab(9)
    expect(router.urls()).toEqual(['/family/api/books/holdings', '/family/api/portfolio/tabs/9'])
  })

  it('sends no CSRF header when there is no cookie, and lets the service say so', async () => {
    setCookie(null)
    const router = routeFetch(
      { '/family/api/logout': fail('csrf', 'Reload the page, then try again.') },
      { '/family/api/logout': 403 }
    )
    await expect(familyApi.logout()).rejects.toThrow('Reload the page, then try again.')
    expect(router.calls[0].headers[CSRF_HEADER]).toBeUndefined()
  })
})

describe('errors are sentences for a trader', () => {
  it('carries the service sentence and its code, never the status', async () => {
    routeFetch(
      {
        '/family/api/login': fail('signin_failed', 'That sign-in name or password did not match.'),
      },
      { '/family/api/login': ERROR_STATUS.signin_failed }
    )
    const error = await familyApi
      .login({ handle: 'h', password: 'p', totp: '1' })
      .catch((caught) => caught)
    expect(error).toBeInstanceOf(FamilyError)
    expect(error.code).toBe('signin_failed')
    expect(error.message).toBe('That sign-in name or password did not match.')
    expect(error.message).not.toMatch(/401|status|HTTP|error/i)
  })

  it('keeps the field a sentence is about', async () => {
    routeFetch({ '/family/api/join': fail('invalid', 'Choose a longer password.', 'password') })
    const error = await familyApi
      .join({ code: 'a', handle: 'h', password: 'p', totp: '1' })
      .catch((caught) => caught)
    expect(error.field).toBe('password')
  })

  it('says the service cannot be reached when the phone is offline', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = await familyApi.me().catch((caught) => caught)
    expect(error.code).toBe('unanswered')
    expect(error.message).toBe(NO_ANSWER)
  })

  it('says it could not read an answer that is not the service', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>502</html>', { status: 502 }))
    )
    const error = await familyApi.me().catch((caught) => caught)
    expect(error.code).toBe('unreadable')
    expect(error.message).not.toMatch(/502|html/i)
  })

  it('tells the shell about a signed-out answer on a session route (not `me`, not a public route)', async () => {
    const heard = vi.fn()
    const stop = onSignedOut(heard)
    const expired = fail('signed_out', 'Your sign-in has expired. Sign in again.')
    routeFetch({
      '/family/api/me': expired,
      '/family/api/login': expired,
      '/family/api/summary': expired,
    })
    // `me` is the shell's own check and does not announce itself (it would loop).
    await familyApi.me().catch(() => undefined)
    expect(heard).not.toHaveBeenCalled()
    await familyApi.login({ handle: 'h', password: 'p', totp: '1' }).catch(() => undefined)
    expect(heard).not.toHaveBeenCalled()
    await familyApi.summary().catch(() => undefined)
    expect(heard).toHaveBeenCalledTimes(1)
    announceSignedOut()
    expect(heard).toHaveBeenCalledTimes(2)
    stop()
  })

  it('never sends the owner CSRF fetch', async () => {
    const router = routeFetch({ '/family/api': ok({}) })
    await familyApi.logout()
    expect(router.urls().some((url) => url.includes('/auth/'))).toBe(false)
  })
})
