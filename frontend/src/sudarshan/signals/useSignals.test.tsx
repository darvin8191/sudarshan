/**
 * The view's hook (U1): `state` read through the bridge at mount, never replacing a newer answer a
 * push brought while the read was on its way; carried on by the clock between answers (going
 * stale, the age, the colour going, a reading lapsing) at the moment each changes; the room joined
 * unless the view is off. Every answer is the real bridge's (bridge.answers.json).
 */
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))
const shared = vi.hoisted(() => ({ socket: null as unknown }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: shared.socket }),
}))

import {
  ANSWERS,
  answerFromRecording,
  findRecorded,
  LATER_MS,
  NOW_MS,
  stateOf,
  viewOf,
} from '@/test/signalsAnswers'
import { STATE_KEY } from './keys'
import { applyPush, resetSignalsLiveForTests, type SignalsView } from './live'
import { EVENT_UPDATE, JOIN_EVENT, type State, WORDS } from './types'
import { lapsedAt, orderReadings, readState, useSignals } from './useSignals'

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

/** Run what is due on the fake clock, and the promises it settles, until the read has answered. */
async function settled(read: () => unknown) {
  for (let n = 0; n < 20 && !read(); n += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5)
    })
  }
  expect(read()).toBeTruthy()
}

function answering(names: readonly string[]) {
  answerFromRecording(webClient, (method, path, query) => findRecorded(method, path, query, names))
}

let client: QueryClient

beforeEach(() => {
  resetSignalsLiveForTests()
  shared.socket = null
  webClient.get.mockReset()
  webClient.request.mockReset()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(() => {
  vi.useRealTimers()
  resetSignalsLiveForTests()
})

describe('one read of state', () => {
  it("takes the bridge's state", async () => {
    answering(['state'])
    const got = await readState(client)
    expect(got).toMatchObject({ kind: 'state', state: ANSWERS.state.answer.data })
  })

  it('keeps a newer state a push brought while the read was on its way', async () => {
    const older = stateOf()
    let finish: (value: unknown) => void = () => {}
    webClient.get.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    const reading = readState(client)
    const pushed = { ...stateOf(), now_ms: older.now_ms + 2_000 }
    applyPush(client, { cause: 'document', state: pushed })
    finish({ data: ANSWERS.state.answer, request: {} })
    expect(await reading).toMatchObject({ state: { now_ms: pushed.now_ms } })
  })

  it('drops "switched off" or a failure that comes back after a push', async () => {
    let finish: (value: unknown) => void = () => {}
    webClient.get.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    const reading = readState(client)
    applyPush(client, { cause: 'document', state: stateOf() })
    finish({ data: ANSWERS.off.answer, request: {} })
    expect(await reading).toMatchObject({ kind: 'state' })

    let fail: (reason: unknown) => void = () => {}
    webClient.get.mockReturnValue(
      new Promise((_resolve, reject) => {
        fail = reject
      })
    )
    const failing = readState(client)
    applyPush(client, { cause: 'document', state: { ...stateOf(), now_ms: NOW_MS + 9 } })
    fail(Object.assign(new Error('Network Error'), { isAxiosError: true }))
    expect(await failing).toMatchObject({ kind: 'state', state: { now_ms: NOW_MS + 9 } })
  })

  it("a failure with no push in between is thrown, in the owner's words", async () => {
    answering(['not_answering'])
    await expect(readState(client)).rejects.toMatchObject({
      code: 'unavailable',
      message: WORDS.not_answering,
    })
  })
})

describe('the view as it stands now', () => {
  it('fresh at 09:05: the header says when, and the age moves by the clock alone', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    answering(['state'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await settled(() => result.current.state)
    expect(result.current.fresh).toMatchObject({
      status: 'fresh',
      words: 'TRINETRA · updated 09:04:40',
      age: 'under a minute',
      colour: true,
    })
    expect(webClient.get).toHaveBeenCalledTimes(1)
    // At 09:05:40 the reading is a minute old: worked out again then, with no read.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(40_000)
    })
    expect(result.current.fresh?.age).toBe('1 minute')
    // fresh_until is next_by + 60 s = 09:10:40: stale then, still with no read.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000)
    })
    expect(result.current.fresh).toMatchObject({
      status: 'stale',
      words: 'TRINETRA has not published since 09:04. This is its last reading, 6 minutes old.',
    })
    expect(webClient.get).toHaveBeenCalledTimes(1)
  })

  it('a gap bias lapses at its 09:15 on the phone, before any push says so', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    answering(['state'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await settled(() => result.current.state)
    const gap = () =>
      result.current.state?.readings.find(
        (each) => each.module === 'gap_bias' && each.instrument === 'NIFTY'
      )
    expect(lapsedAt(gap()!, result.current.now)).toBe(false)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000)
    })
    expect(lapsedAt(gap()!, result.current.now)).toBe(true)
  })

  it("the server's clock, not the phone's, decides: a phone running five minutes ahead", async () => {
    vi.useFakeTimers({ now: NOW_MS + 5 * 60_000, toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    answering(['state'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await settled(() => result.current.state)
    expect(result.current.now).toBe(NOW_MS)
    expect(result.current.fresh?.status).toBe('fresh')
  })

  it('switched off: the sentence, no state, and the room let go once that is known', async () => {
    // The room is asked for at mount, before the first answer, so no push between the read and
    // the join is missed; an answer of "switched off" lets it go.
    const emit = vi.fn()
    const off = vi.fn()
    shared.socket = { connected: true, on: vi.fn(), off, emit, timeout: () => ({ emit }) }
    answering(['off'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.off).toBe(WORDS.off))
    expect(result.current.state).toBeNull()
    await waitFor(() => expect(off).toHaveBeenCalledWith(EVENT_UPDATE, expect.any(Function)))
  })

  // Review v0.15.0 L7: off is read once per visit, not on every return to the app after 30 s.
  it('switched off: not read again on a return to the app, however long after', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    answering(['off'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.off).toBe(WORDS.off))
    expect(webClient.get).toHaveBeenCalledTimes(1)
    vi.setSystemTime(NOW_MS + 10 * 60_000)
    await act(async () => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
      await Promise.resolve()
    })
    // A second screen that shows the view (the Dashboard's chip, then /trinetra) asks nothing.
    renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await act(async () => {
      await Promise.resolve()
    })
    expect(webClient.get).toHaveBeenCalledTimes(1)
    // Try again still asks.
    await act(async () => {
      result.current.refetch()
      await Promise.resolve()
    })
    expect(webClient.get).toHaveBeenCalledTimes(2)
    focusManager.setFocused(undefined)
  })

  it('on: a return to the app after 30 s reads the state again, as before', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    answering(['state'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.state).not.toBeNull())
    vi.setSystemTime(NOW_MS + 31_000)
    await act(async () => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
      await Promise.resolve()
    })
    await waitFor(() => expect(webClient.get).toHaveBeenCalledTimes(2))
    focusManager.setFocused(undefined)
  })

  it('on: the room is joined on the shared connection', async () => {
    const emit = vi.fn()
    shared.socket = { connected: true, on: vi.fn(), off: vi.fn(), emit, timeout: () => ({ emit }) }
    answering(['state'])
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.state).not.toBeNull())
    expect(emit).toHaveBeenCalledWith(JOIN_EVENT, expect.any(Function))
    expect(result.current.connected).toBe(true)
  })

  it('not answering: the problem in words, and what was shown before stays', async () => {
    answering(['not_answering'])
    client.setQueryData<SignalsView>(STATE_KEY, viewOf(stateOf('state_stale'), LATER_MS))
    const { result } = renderHook(() => useSignals(), { wrapper: wrapper(client) })
    await act(async () => {
      await client.invalidateQueries({ queryKey: STATE_KEY })
    })
    await waitFor(() => expect(result.current.problem?.message).toBe(WORDS.not_answering))
    expect(result.current.state).not.toBeNull()
  })
})

describe('the order a screen lists the readings in', () => {
  it('known modules first in their order, NIFTY before BANKNIFTY, lapsed ones below', () => {
    const state: State = stateOf('state_stale')
    const order = orderReadings(state.readings, LATER_MS).map(
      (each) => `${each.module}:${each.instrument}`
    )
    expect(order).toEqual([
      'regime:NIFTY',
      'regime:BANKNIFTY',
      'swing_eod:NIFTY',
      'swing_intraday:BANKNIFTY',
      'breadth:NIFTY',
      'gap_bias:NIFTY',
      'gap_bias:BANKNIFTY',
    ])
  })
})
