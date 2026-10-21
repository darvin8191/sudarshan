/**
 * What an exit's or a cancel's answer says about the order (M6.9): only the
 * app itself can refuse; no answer of its own leaves the order possibly sent.
 */
import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'
import { appAnswered, NOT_CONFIRMED, refusalReason, thrownOutcome } from './sendOutcome'

function answered(status: number, data: unknown) {
  return new AxiosError('Request failed', 'ERR_BAD_RESPONSE', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data,
  })
}

describe('a send that threw', () => {
  it('was refused when the app answered no, whatever the code', () => {
    for (const status of [400, 401, 403, 404, 409, 422, 429]) {
      expect(thrownOutcome(answered(status, { status: 'error', message: 'No' }))).toBe('refused')
    }
    // Flask's own refusal of a missing security token is a page, not JSON: still a refusal.
    expect(thrownOutcome(answered(400, '<p>The CSRF token is missing.</p>'))).toBe('refused')
    expect(thrownOutcome(answered(500, { status: 'error', message: 'Broker said no' }))).toBe(
      'refused'
    )
  })

  it('was refused when it never left: the security token step before it failed', () => {
    expect(thrownOutcome(new Error('Failed to fetch CSRF token.'))).toBe('refused')
  })

  it('is not confirmed when no answer of the app’s own came back', () => {
    expect(thrownOutcome(new AxiosError('Network Error', AxiosError.ERR_NETWORK))).toBe(
      'unconfirmed'
    )
    expect(thrownOutcome(new AxiosError('timeout', AxiosError.ECONNABORTED))).toBe('unconfirmed')
    for (const status of [502, 503, 504]) {
      expect(thrownOutcome(answered(status, { status: 'error', message: 'x' }))).toBe('unconfirmed')
    }
    expect(thrownOutcome(answered(500, '<h1>Internal Server Error</h1>'))).toBe('unconfirmed')
  })
})

describe('an answer', () => {
  it('is the app’s own only as a JSON object with its status word', () => {
    expect(appAnswered({ status: 'error', message: 'No' })).toBe(true)
    expect(appAnswered({ message: 'No' })).toBe(false)
    expect(appAnswered('<html></html>')).toBe(false)
    expect(appAnswered(null)).toBe(false)
  })

  it('gives the app’s reason for a refusal, when it gave one', () => {
    expect(refusalReason(answered(400, { status: 'error', message: '  Market is closed ' }))).toBe(
      'Market is closed'
    )
    expect(refusalReason(answered(400, { status: 'error', message: '' }))).toBeNull()
    expect(refusalReason(answered(400, '<p>page</p>'))).toBeNull()
    expect(refusalReason(new Error('x'))).toBeNull()
  })

  it('with none, never tells the trader the order failed', () => {
    for (const text of Object.values(NOT_CONFIRMED)) {
      expect(text).toMatch(/not confirmed/)
      expect(text).not.toMatch(/fail/i)
    }
  })
})
