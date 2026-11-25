/**
 * v0.18.0 verification V1: the list's quotes are read again once when a market opens and once when
 * it closes, whatever React renders.
 *
 * The page works out "open" from the clock while it renders (`useMarketStatus` reads `Date.now()`),
 * not from React state. React may render the page and then throw that render away because nothing in
 * its state changed (two updates that cancel out). It keeps the new dependencies of that render's
 * effects but not the effects themselves, so an effect keyed on the open state saw the change once,
 * in a render that was dropped, and never again: the read at the open or the close was lost for the
 * rest of the session. Here the bailout is forced, so the test does not depend on timer order.
 */
import { act, renderHook } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args) },
}))

import { type Instrument, useQuoteSnapshot } from './useQuoteSnapshot'

const LIST: Instrument[] = [{ symbol: 'SBIN', exchange: 'NSE' }]

/** The market as the page has it: read from the clock while rendering, not from React state. */
const market: { open: boolean | null } = { open: true }

/** The page around the hook: a value it keeps, how often it rendered, and how often React committed it. */
const page = { value: 0, set: (_value: number) => {}, renders: 0, commits: 0 }

function usePage() {
  const [value, set] = useState(0)
  page.value = value
  page.set = set
  page.renders += 1
  useEffect(() => {
    page.commits += 1
  })
  return useQuoteSnapshot({
    apiKey: 'key',
    instruments: LIST,
    connectionEpoch: 1,
    marketOpen: market.open,
  })
}

/**
 * Two updates that cancel out in one act: React renders the page, finds no state changed, and throws
 * the render away (its effects do not run). Checked, so the test cannot pass without the bailout.
 */
function bailout() {
  const renders = page.renders
  const commits = page.commits
  const value = page.value
  act(() => {
    page.set(value + 1)
    page.set(value)
  })
  expect(page.renders).toBe(renders + 1)
  expect(page.commits).toBe(commits)
}

/** The clock's one-second tick: the time moves on, and a render that changes something is committed. */
function tick() {
  const commits = page.commits
  const value = page.value
  act(() => {
    vi.advanceTimersByTime(1000)
    page.set(value + 1)
  })
  expect(page.commits).toBe(commits + 1)
}

const settle = () => act(() => vi.advanceTimersByTimeAsync(0))
const reads = () => getMultiQuotes.mock.calls.length

beforeEach(() => {
  vi.useFakeTimers()
  market.open = true
  page.renders = 0
  page.commits = 0
  getMultiQuotes.mockReset()
  getMultiQuotes.mockImplementation(async (_key: string, asked: Instrument[]) => ({
    status: 'success',
    results: asked.map((each) => ({ ...each, data: { ltp: 100, prev_close: 99 } })),
  }))
})
afterEach(() => {
  vi.useRealTimers()
})

describe('the quote snapshot at a market open or close', () => {
  it('reads once at the close and once at the open, though React threw away the render that saw it', async () => {
    renderHook(usePage)
    await settle()
    expect(reads()).toBe(1)

    // The close. The first render to see it is one React throws away, then the clock ticks.
    market.open = false
    bailout()
    expect(reads()).toBe(1)
    tick()
    await settle()
    expect(reads()).toBe(2)

    // The open, the same way.
    market.open = true
    bailout()
    tick()
    await settle()
    expect(reads()).toBe(3)
  })

  it('reads exactly once a change, and nothing at any other render (the quiet path)', async () => {
    const view = renderHook(usePage)
    await settle()
    expect(reads()).toBe(1)

    // A minute of ticks and thrown-away renders, the market staying open: no read.
    for (let second = 0; second < 60; second += 1) {
      tick()
      if (second % 5 === 0) bailout()
      view.rerender()
    }
    await settle()
    expect(reads()).toBe(1)

    // The close: one read, however many renders follow it, thrown away or kept.
    market.open = false
    bailout()
    bailout()
    for (let second = 0; second < 30; second += 1) {
      tick()
      bailout()
    }
    await settle()
    expect(reads()).toBe(2)

    // The open, the same way.
    market.open = true
    tick()
    bailout()
    for (let second = 0; second < 30; second += 1) tick()
    await settle()
    expect(reads()).toBe(3)
  })

  it('reads nothing when the calendar first becomes known, or stops being known for a while', async () => {
    market.open = null
    renderHook(usePage)
    await settle()
    expect(reads()).toBe(1)

    // The calendar arrives: unknown to known is no change.
    market.open = true
    bailout()
    tick()
    // It stops being known (a new day before the calendar is read again), then is known again.
    market.open = null
    tick()
    market.open = false
    bailout()
    tick()
    await settle()
    expect(reads()).toBe(1)
  })
})
