/**
 * The TRINETRA view's client (U1) against OpenAlgo's real bridge: every answer here was recorded
 * from the bridge in front of the real sudarshan-signals (bridge.answers.json). The page builds the
 * same route, method and query the recording asked with, takes a success's data as it came, says
 * the view switched off in the service's sentence, and shows every refusal in the bridge's sentence
 * with its code. What no envelope carries (no answer, a page in place of the envelope) gets the
 * page's own words, and a write that may have arrived is never said to have failed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))

import {
  ANSWERS,
  answerFromRecording,
  findRecorded,
  type Recorded,
  refusal,
} from '@/test/signalsAnswers'
import {
  CODE_WORDS,
  PAGE_WORDS,
  problemOf,
  retryable,
  SignalsError,
  sentenceOf,
  signalsCall,
  signalsData,
} from './api'
import { type OpName, WORDS } from './types'

/** Answer every request from the named recordings (all of them unless said). */
function bridge(names?: readonly string[]) {
  answerFromRecording(webClient, (method, path, query) => findRecorded(method, path, query, names))
}

async function call(entry: Recorded) {
  return signalsCall(entry.op as OpName, entry.args as never)
}

beforeEach(() => {
  webClient.get.mockReset()
  webClient.request.mockReset()
})

describe('the page and the bridge as built, answer by answer', () => {
  const entries = Object.entries(ANSWERS)

  it('has the recording of every op', () => {
    expect(new Set(entries.map(([, entry]) => entry.op))).toEqual(
      new Set(['state', 'timeline', 'change_get', 'performance', 'settings_get', 'settings_set'])
    )
  })

  for (const [name, entry] of entries) {
    it(`${name}: ${entry.method} ${entry.path} answered ${entry.status}`, async () => {
      bridge([name])
      const answer = entry.answer
      if (answer.status === 'success' && answer.enabled !== false) {
        await expect(call(entry)).resolves.toEqual({ enabled: true, data: answer.data })
      } else if (answer.status === 'success') {
        await expect(call(entry)).resolves.toEqual({
          enabled: false,
          data: null,
          message: answer.message,
        })
      } else {
        const thrown = await call(entry).catch((error: unknown) => error)
        expect(thrown).toBeInstanceOf(SignalsError)
        const { code, message, field } = thrown as SignalsError
        expect({ code, message, field }).toEqual({
          code: answer.error?.code,
          message: answer.error?.message,
          field: answer.error?.field ?? null,
        })
        // Every sentence the bridge sent is the contract's, so it is the one shown.
        expect(sentenceOf(answer.error?.message)).toBe(answer.error?.message)
      }
      // The route the page asked is the one the bridge was asked: nothing went anywhere else.
      const asked = [...webClient.get.mock.calls, ...webClient.request.mock.calls]
      expect(asked).toHaveLength(1)
    })
  }

  it('sends the write as the JSON body of a PUT, the id never in the body', async () => {
    bridge(['settings_saved'])
    await signalsCall('settings_set', { version: 1, lang: 'gu' })
    expect(webClient.request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/sudarshan/signals/api/settings',
        method: 'PUT',
        data: { version: 1, lang: 'gu' },
      })
    )
  })

  it('reads a page of the timeline with its cursor in the query', async () => {
    bridge(['timeline_older'])
    const older = ANSWERS.timeline_older
    const page = await signalsData('timeline', older.args as never)
    expect(page).toEqual(older.answer.data)
    expect(webClient.get.mock.calls[0][1].params).toEqual(older.query)
  })
})

describe('what no envelope carries', () => {
  it('no answer to a read: check the phone, then try again', async () => {
    webClient.get.mockRejectedValue(
      Object.assign(new Error('Network Error'), { isAxiosError: true })
    )
    await expect(signalsCall('state')).rejects.toMatchObject({
      code: 'unanswered',
      message: PAGE_WORDS.unanswered,
    })
  })

  it('no answer to the write: not confirmed, never "failed"', async () => {
    webClient.request.mockRejectedValue(
      Object.assign(new Error('timeout'), { isAxiosError: true, code: 'ECONNABORTED' })
    )
    await expect(signalsCall('settings_set', { version: 1, lang: 'en' })).rejects.toMatchObject({
      code: 'not_confirmed',
      message: WORDS.not_confirmed,
    })
  })

  it('a server without the bridge (a 404 page): this part is not ready', async () => {
    webClient.get.mockRejectedValue(refusal(404, '<!doctype html><title>Not Found</title>'))
    await expect(signalsCall('performance')).rejects.toMatchObject({
      code: 'not_ready',
      message: WORDS.not_ready,
    })
  })

  it('a gateway page in place of the envelope: the reader is not answering, or "failed"', async () => {
    webClient.get.mockRejectedValue(refusal(503, '<html>Service Unavailable</html>'))
    await expect(signalsCall('state')).rejects.toMatchObject({
      code: 'unavailable',
      message: WORDS.not_answering,
    })
    webClient.get.mockRejectedValue(refusal(502, 'Bad Gateway'))
    await expect(signalsCall('state')).rejects.toMatchObject({
      code: 'unavailable',
      message: WORDS.failed,
    })
  })

  it('an ended session: a 401, a CSRF refusal or a landing on the login page', async () => {
    webClient.get.mockRejectedValue(refusal(401, ''))
    await expect(signalsCall('state')).rejects.toMatchObject({ code: 'unauthorized' })
    webClient.request.mockRejectedValue(refusal(400, '<p>The CSRF token has expired.</p>'))
    await expect(signalsCall('settings_set', { version: 1 })).rejects.toMatchObject({
      code: 'unauthorized',
      message: WORDS.unauthorized,
    })
    webClient.get.mockResolvedValue({
      data: '<html>login</html>',
      request: { responseURL: 'http://localhost/login' },
    })
    await expect(signalsCall('state')).rejects.toMatchObject({ code: 'unauthorized' })
  })

  it('a success that is not the envelope is read as "failed", never shown', async () => {
    webClient.get.mockResolvedValue({ data: '<html>index</html>', request: {} })
    await expect(signalsCall('state')).rejects.toMatchObject({
      code: 'unreadable',
      message: WORDS.failed,
    })
  })

  it('shows only the contract sentences: anything else becomes the sentence of its code', async () => {
    webClient.get.mockRejectedValue(
      refusal(503, {
        status: 'error',
        error: { code: 'unavailable', message: 'Traceback (most recent call last): <b>boom</b>' },
      })
    )
    await expect(signalsCall('state')).rejects.toMatchObject({
      code: 'unavailable',
      message: CODE_WORDS.unavailable,
    })
    webClient.get.mockRejectedValue(
      refusal(429, { status: 'error', error: { code: 'something_new', message: 'x' } })
    )
    await expect(signalsCall('state')).rejects.toMatchObject({
      code: 'rate_limited',
      message: WORDS.rate_limited,
    })
    expect(sentenceOf('HTTP 500')).toBeNull()
    expect(sentenceOf(WORDS.held)).toBe(WORDS.held)
  })

  it('the view switched off with a sentence the page does not know: "switched off"', async () => {
    webClient.get.mockResolvedValue({
      data: { status: 'success', enabled: false, data: null, message: 'switched_off' },
      request: {},
    })
    await expect(signalsCall('state')).resolves.toEqual({
      enabled: false,
      data: null,
      message: WORDS.off,
    })
  })

  it('a read while the view is off throws the sentence for the part that asked', async () => {
    bridge(['off'])
    await expect(signalsData('state')).rejects.toMatchObject({ code: 'off', message: WORDS.off })
  })

  it('reads again only where that can help', () => {
    expect(retryable(new SignalsError({ code: 'unavailable', message: '', field: null }))).toBe(
      true
    )
    expect(retryable(new SignalsError({ code: 'unanswered', message: '', field: null }))).toBe(true)
    expect(retryable(new SignalsError({ code: 'forbidden', message: '', field: null }))).toBe(false)
    expect(
      retryable(new SignalsError({ code: 'version_conflict', message: '', field: null }))
    ).toBe(false)
    expect(problemOf('not an error').code).toBe('unsent')
  })
})
