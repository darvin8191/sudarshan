import { AxiosError } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient: client }))

import { JournalError, journalCall, journalData, problemOf, reportFileUrl, WORDS } from './api'

const PREFIX = '/sudarshan/journal/api'

beforeEach(() => {
  client.get.mockReset()
  client.request.mockReset()
})

function refused(status: number, data: unknown) {
  return new AxiosError('x', 'ERR', undefined, {}, {
    status,
    data,
    statusText: '',
    headers: {},
    config: {} as never,
  } as never)
}

describe('journalCall', () => {
  it('reads with its query, without the route’s number and without empty values', async () => {
    client.get.mockResolvedValue({ data: { status: 'success', enabled: true, data: [] } })
    const result = await journalCall('trips', { mode: 'sandbox', status: 'open', strategy: '' })
    expect(result).toEqual({ enabled: true, data: [] })
    const [url, config] = client.get.mock.calls[0]
    expect(url).toBe(`${PREFIX}/trips`)
    expect(config.params).toEqual({ mode: 'sandbox', status: 'open' })
    expect(config.timeout).toBeGreaterThan(3000)
  })

  it('puts a trade’s number in the route and sends the rest of a write as its body', async () => {
    client.request.mockResolvedValue({
      data: { status: 'success', enabled: true, data: { id: 7, version: 4, words: null } },
    })
    await journalCall('trip_update', { id: 7, version: 3, notes: 'Waited for the retest.' })
    const config = client.request.mock.calls[0][0]
    expect(config.url).toBe(`${PREFIX}/trips/7`)
    expect(config.method).toBe('PATCH')
    expect(config.data).toEqual({ version: 3, notes: 'Waited for the retest.' })
  })

  it('answers switched off as a success that says so', async () => {
    client.get.mockResolvedValue({ data: { status: 'success', enabled: false, data: null } })
    expect(await journalCall('state')).toEqual({
      enabled: false,
      data: null,
      message: WORDS.off,
    })
    client.get.mockResolvedValue({
      data: { status: 'success', enabled: false, message: 'Switched off here.' },
    })
    expect(await journalCall('state')).toMatchObject({ message: 'Switched off here.' })
  })

  it('throws the bridge’s own sentence, and never a status code', async () => {
    client.get.mockRejectedValue(
      refused(502, {
        status: 'error',
        error: { code: 'upstream', message: 'The journal service is not answering. Try again.' },
      })
    )
    const error = await journalCall('state').catch((e) => e)
    expect(error).toBeInstanceOf(JournalError)
    expect(error.message).toBe('The journal service is not answering. Try again.')
  })

  it.each([
    [401, 'unauthorized', WORDS.signedOut],
    [409, 'conflict', WORDS.changedElsewhere],
    [404, 'unavailable', WORDS.notReady],
    [500, 'unavailable', WORDS.unavailable],
  ])('says status %i in words when the answer carries none', (status, code, words) => {
    const problem = problemOf(refused(status, ''))
    expect(problem).toEqual({ code, message: words })
    expect(problem.message).not.toMatch(/\b(40\d|50\d|HTTP|JSON|endpoint)\b/i)
  })

  it('says a conflict in the one sentence even when the bridge words it differently', () => {
    const problem = problemOf(
      refused(409, { status: 'error', error: { code: 'conflict', message: 'stale version 3' } })
    )
    expect(problem).toEqual({ code: 'conflict', message: WORDS.changedElsewhere })
  })

  it('says a CSRF refusal as the session having ended', () => {
    expect(problemOf(refused(400, 'The CSRF token has expired.')).code).toBe('unauthorized')
  })

  it('says no answer and an unreadable one in words', async () => {
    client.get.mockRejectedValue(new AxiosError('x', 'ECONNABORTED'))
    expect((await journalCall('state').catch((e) => e)).message).toBe(WORDS.noAnswer)
    client.get.mockResolvedValue({ data: '<html></html>' })
    expect((await journalCall('state').catch((e) => e)).message).toBe(WORDS.unreadable)
  })

  it('journalData throws the off sentence for a read that cannot go on', async () => {
    client.get.mockResolvedValue({ data: { status: 'success', enabled: false, data: null } })
    await expect(journalData('state')).rejects.toThrow(WORDS.off)
  })

  it('names the bridge’s file route for a download, and its CSV form', () => {
    expect(reportFileUrl(12)).toBe(`${PREFIX}/reports/12/file`)
    expect(reportFileUrl(12, 'csv')).toBe(`${PREFIX}/reports/12/file?format=csv`)
  })
})
