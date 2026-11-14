/**
 * The same strike's other expiries (M8.11): what the read's failure tells the sheet that shows it.
 * A server with the watchlists switch off refuses for good (`off`), so a host may leave the section
 * out; any other failure is one to try again, and Try again reads again.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// A plain function, not a spy: a spy that answers with a rejected promise fails the test that
// made it, although the hook under test catches the rejection.
const api = vi.hoisted(() => ({ answers: [] as Array<() => Promise<unknown>> }))
vi.mock('./v2/api', async (original) => ({
  ...(await original<typeof import('./v2/api')>()),
  callOp: () => (api.answers.length > 1 ? api.answers.shift() : api.answers[0])?.(),
}))

import { useSameStrike } from './useSameStrike'
import { WatchlistsError } from './v2/api'

const ARGS = { underlying: 'TEST', exchange: 'NSE_INDEX', strike: 25_900, after: '2026-11-17' }
const ANSWER = {
  expiry: '2026-11-17',
  expiries: [{ date: '2026-11-17' }, { date: '2026-11-24' }],
  strikes: [{ strike: 25_900, ce: null, pe: null }],
}

const refuse = (code: 'off' | 'unanswered' | 'busy') => () =>
  Promise.reject(new WatchlistsError(code, 'The server\'s sentence.'))

beforeEach(() => {
  api.answers = []
})

describe('useSameStrike', () => {
  it('names a switched-off server apart from a read that failed in passing', async () => {
    api.answers = [refuse('off')]
    const off = renderHook(() => useSameStrike(ARGS))
    await waitFor(() => expect(off.result.current.state).toBe('error'))
    expect(off.result.current.off).toBe(true)
  })

  it('takes any other failure as one to try again', async () => {
    api.answers = [refuse('unanswered')]
    const passing = renderHook(() => useSameStrike(ARGS))
    await waitFor(() => expect(passing.result.current.state).toBe('error'))
    expect(passing.result.current.off).toBe(false)
  })

  it('reads again on Try again, and shows what the second read brought', async () => {
    api.answers = [refuse('busy'), async () => ANSWER]
    const hook = renderHook(() => useSameStrike(ARGS))
    await waitFor(() => expect(hook.result.current.state).toBe('error'))
    act(() => hook.result.current.retry())
    expect(hook.result.current.state).toBe('loading')
    await waitFor(() => expect(hook.result.current.state).toBe('ready'))
    expect(hook.result.current.off).toBe(false)
    expect(hook.result.current.rows.map((row) => row.expiry)).toEqual(['2026-11-24'])
  })
})
