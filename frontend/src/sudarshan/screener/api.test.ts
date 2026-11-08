import { AxiosError, type AxiosResponse } from 'axios'
import { describe, expect, it } from 'vitest'
import { problemOf, ScreenerError, sessionEnded } from './api'
import { WORDS } from './types'

function failure(status: number, data: unknown, url?: string) {
  const response = { status, data, request: { responseURL: url } } as unknown as AxiosResponse
  return new AxiosError('x', 'ERR_BAD_REQUEST', undefined, undefined, response)
}

describe('what a failed request says', () => {
  it('keeps the server sentence and its code as they came', () => {
    expect(
      problemOf(
        failure(409, {
          status: 'error',
          error: { code: 'version_conflict', message: 'Changed.', field: 'name' },
        })
      )
    ).toEqual({
      code: 'conflict',
      message: 'Changed.',
      field: 'name',
    })
  })
  it('says no answer came when the request got none', () => {
    expect(problemOf(new AxiosError('Network Error'))).toMatchObject({
      code: 'unanswered',
      message: WORDS.unanswered,
    })
  })
  it('says the session ended for a 401, a CSRF refusal and a login redirect', () => {
    expect(problemOf(failure(401, '')).message).toBe(WORDS.session_ended)
    expect(problemOf(failure(400, 'The CSRF token has expired.')).message).toBe(WORDS.session_ended)
    expect(sessionEnded(200, null, '/auth/login')).toBe(true)
    expect(sessionEnded(200, null, '/screener')).toBe(false)
  })
  it('translates the service codes to the ones the page acts on, and keeps the rest', () => {
    const code = (service: string) =>
      problemOf(failure(422, { status: 'error', error: { code: service, message: 'Words.' } })).code
    expect(code('refused')).toBe('invalid')
    expect(code('not_found')).toBe('gone')
    expect(code('not_confirmed')).toBe('unanswered')
    expect(code('unavailable')).toBe('failed')
    expect(code('rate_limited')).toBe('rate_limited')
    expect(code('forbidden')).toBe('forbidden')
  })

  it('never shows a status code or a protocol word for an answer it cannot read', () => {
    const problem = problemOf(failure(502, '<html>Bad Gateway</html>'))
    expect(problem).toMatchObject({ code: 'unreadable', message: WORDS.unreadable })
    expect(problem.message).not.toMatch(/\b50\d\b|HTTP|JSON/)
  })
  it('passes an error of its own through', () => {
    const own = new ScreenerError({ code: 'gone', message: 'Gone.', field: null })
    expect(problemOf(own)).toEqual({ code: 'gone', message: 'Gone.', field: null })
  })
  it('says the request was not sent for anything that is not a request failure', () => {
    expect(problemOf(new Error('boom')).code).toBe('unsent')
  })
})
