import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { webClient } from '@/api/client'
import { answer } from '@/test/axiosAnswer'
import { cardOf } from './owner/fixtures'
import { newRequestId, OwnerError, ownerApi, ownerCall, ownerProblem } from './ownerApi'

let seen: Array<{ method?: string; url?: string; params?: unknown; data?: unknown; csrf?: unknown }>
let adapter: ReturnType<typeof answer>
const original = webClient.defaults.adapter

function reply(status: number, body: unknown) {
  adapter = answer(status, body)
  webClient.defaults.adapter = async (config) => {
    seen.push({
      method: config.method,
      url: config.url,
      params: config.params,
      data: config.data,
      csrf: config.headers?.['X-CSRFToken'],
    })
    return adapter(config)
  }
}

beforeEach(() => {
  seen = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ csrf_token: 'tok-1' })))
  )
})
afterEach(() => {
  webClient.defaults.adapter = original
  vi.unstubAllGlobals()
})

describe('the owner client', () => {
  it('reads state, member_list, device_list, audit_list and share_list with a GET and a query', async () => {
    reply(200, { status: 'success', data: [] })
    await ownerApi.memberList()
    await ownerApi.deviceList(4)
    await ownerApi.auditList({ member_id: 4, kind: 'login_ok', before: null, limit: 200 })
    await ownerApi.shareList()
    expect(seen.map((s) => [s.method, s.url])).toEqual([
      ['get', '/sudarshan/family/api/member_list'],
      ['get', '/sudarshan/family/api/device_list'],
      ['get', '/sudarshan/family/api/audit_list'],
      ['get', '/sudarshan/family/api/share_list'],
    ])
    expect(seen[1].params).toEqual({ member_id: '4' })
    // a null or empty field is not sent at all
    expect(seen[2].params).toEqual({ member_id: '4', kind: 'login_ok', limit: '200' })
  })

  it('writes with a POST and a JSON body, carrying the app CSRF token', async () => {
    reply(200, { status: 'success', data: cardOf() })
    await ownerApi.memberUpdate({ id: 1, version: 3, display_name: 'Name' })
    expect(seen[0].method).toBe('post')
    expect(seen[0].url).toBe('/sudarshan/family/api/member_update')
    expect(seen[0].data).toBe(JSON.stringify({ id: 1, version: 3, display_name: 'Name' }))
    expect(seen[0].csrf).toBe('tok-1')
  })

  it('passes the switched-off record on as data, not as an error', async () => {
    reply(200, {
      status: 'success',
      data: { enabled: false, message: 'The family view is switched off.' },
    })
    const result = await ownerCall('member_list')
    expect(result).toEqual({
      enabled: false,
      data: null,
      message: 'The family view is switched off.',
    })
    await expect(ownerApi.memberList()).rejects.toMatchObject({
      code: 'off',
      message: 'The family view is switched off.',
    })
  })

  it('reads state as an answer of its own even when it says enabled: false', async () => {
    const state = {
      enabled: false,
      message: 'Switched off.',
      store_version: 2,
      members: 0,
      sessions: 0,
      sockets: 0,
    }
    reply(200, { status: 'success', data: state })
    expect(await ownerApi.state()).toEqual({ enabled: true, data: state })
  })

  it('carries the service sentence of an error, and the field it is about', async () => {
    reply(400, {
      status: 'error',
      code: 'invalid',
      message: 'Give the member a name.',
      field: 'display_name',
    })
    const error = await ownerApi.memberCreate({ display_name: '', role: 'viewer' }).catch((e) => e)
    expect(error).toBeInstanceOf(OwnerError)
    expect(error.message).toBe('Give the member a name.')
    expect(error.field).toBe('display_name')
    expect(error.unconfirmed).toBe(false)
  })

  it('says not confirmed, and never failed, for a write the bridge sent and had no answer to', async () => {
    reply(504, {
      status: 'error',
      code: 'not_confirmed',
      message: 'Not confirmed: open the Family page again to see whether it was done.',
    })
    const error = await ownerApi.memberRemove({ id: 1, version: 1 }).catch((e) => e)
    expect(error.unconfirmed).toBe(true)
    expect(error.message).toMatch(/^Not confirmed/)
    expect(error.message).not.toMatch(/fail/i)
  })

  it('puts no status code, class or address in front of the owner when no sentence came', async () => {
    reply(502, '<html>Bad Gateway</html>')
    const error = ownerProblem(await ownerApi.memberList().catch((e) => e))
    expect(error.code).toBe('unreadable')
    expect(error.message).not.toMatch(/\d{3}|Error|http|\/sudarshan/i)
  })

  it('treats an answer that never came as unconfirmed for a write', async () => {
    webClient.defaults.adapter = async () => {
      throw Object.assign(new Error('Network Error'), { isAxiosError: true, response: undefined })
    }
    const error = await ownerApi
      .memberStatus({ id: 1, version: 1, status: 'suspended' })
      .catch((e) => e)
    expect(error.code).toBe('unanswered')
    expect(error.unconfirmed).toBe(true)
  })

  it('makes a different request id every time', () => {
    expect(newRequestId()).not.toBe(newRequestId())
  })
})
