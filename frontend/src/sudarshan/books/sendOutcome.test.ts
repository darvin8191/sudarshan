/**
 * What an exit's or a cancel's answer says about the order (M6.9): anything
 * that may have reached the broker is not confirmed; only a 4xx the app gives
 * before anything was sent, or a request that never left, is a refusal.
 */
import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'
import {
  appAnswered,
  NOT_CONFIRMED,
  notConfirmed,
  refusalReason,
  thrownOutcome,
} from './sendOutcome'

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
  it('was refused when the app answered no with a 4xx: nothing was sent', () => {
    for (const status of [400, 401, 403, 404, 409, 422, 429]) {
      expect(thrownOutcome(answered(status, { status: 'error', message: 'No' }))).toBe('refused')
    }
    // Flask's own refusal of a missing security token is a page, not JSON: still a refusal.
    expect(thrownOutcome(answered(400, '<p>The CSRF token is missing.</p>'))).toBe('refused')
  })

  it('is not confirmed on any 5xx, the app’s own JSON too: it may have reached the broker', () => {
    // A read timeout after Upstox took the order is the app's JSON 500; a broker answer
    // naming no order is its 502.
    for (const status of [500, 502, 503, 504]) {
      expect(thrownOutcome(answered(status, { status: 'error', message: 'timed out' }))).toBe(
        'unconfirmed'
      )
    }
  })

  it('is not confirmed when the app marks it so, whatever the code', () => {
    const someSent = { status: 'error', message: '1 of 2 not squared off', not_confirmed: true }
    expect(thrownOutcome(answered(429, someSent))).toBe('unconfirmed')
    expect(notConfirmed(someSent)).toBe(true)
    expect(notConfirmed({ status: 'error', not_confirmed: 'yes' })).toBe(false)
    expect(notConfirmed({ status: 'error' })).toBe(false)
  })

  it('was refused when it never left: the security token step before it failed', () => {
    expect(thrownOutcome(new Error('Failed to fetch CSRF token.'))).toBe('refused')
  })

  it('has no answer when none of the app’s own came back: the server may still be at it', () => {
    expect(thrownOutcome(new AxiosError('Network Error', AxiosError.ERR_NETWORK))).toBe(
      'unanswered'
    )
    expect(thrownOutcome(new AxiosError('timeout', AxiosError.ECONNABORTED))).toBe('unanswered')
    // A page from something in between (nginx gave up, or the app was restarting).
    for (const status of [500, 502, 503, 504]) {
      expect(thrownOutcome(answered(status, '<h1>Gateway Time-out</h1>'))).toBe('unanswered')
    }
    // The app's own 5xx: its view ended, and the order may have gone.
    for (const status of [502, 503, 504]) {
      expect(thrownOutcome(answered(status, { status: 'error', message: 'x' }))).toBe('unconfirmed')
    }
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

  it('when not confirmed, never tells the trader the order failed, nor why it broke', () => {
    for (const text of Object.values(NOT_CONFIRMED)) {
      expect(text).toMatch(/not confirmed: (it|some \w+) may have gone through/)
      expect(text).not.toMatch(/fail|answer|timed out|error/i)
    }
  })
})
