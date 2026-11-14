/**
 * SCR-F: the list's REST read of quotes is never made while the page is
 * hidden. The first snapshot, the read on a reconnect, at a market's open or
 * close and the re-sync of quiet rows all stay; a hidden page only owes them,
 * and makes one read when it is shown again.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args) },
}))

import { AWAY_MS, type Instrument, useQuoteSnapshot } from './useQuoteSnapshot'

const SBIN = { symbol: 'SBIN', exchange: 'NSE' }
const INFY = { symbol: 'INFY', exchange: 'NSE' }

const answer = (instruments: Instrument[]) => ({
  status: 'success',
  results: instruments.map((each) => ({ ...each, data: { ltp: 100, prev_close: 99 } })),
})

let visibility: DocumentVisibilityState = 'visible'
function page(state: DocumentVisibilityState) {
  visibility = state
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}
const settle = () => act(() => vi.advanceTimersByTimeAsync(0))
const pass = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms))
/** The symbols each read asked for, in order. */
const reads = () =>
  getMultiQuotes.mock.calls.map(([, asked]) =>
    (asked as Instrument[]).map((each) => each.symbol).join(',')
  )

interface Props {
  instruments: Instrument[]
  connectionEpoch: number
  marketOpen: boolean | null
}
const mount = (props: Props) =>
  renderHook((now: Props) => useQuoteSnapshot({ apiKey: 'key', ...now }), { initialProps: props })

beforeEach(() => {
  vi.useFakeTimers()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibility,
  })
  getMultiQuotes.mockReset()
  getMultiQuotes.mockImplementation(async (_key: string, asked: Instrument[]) => answer(asked))
})
afterEach(() => {
  Reflect.deleteProperty(document, 'visibilityState')
  vi.useRealTimers()
})

describe('the quote snapshot on a hidden page', () => {
  it('reads nothing while hidden, whatever happens, and reads once when shown', async () => {
    const view = mount({ instruments: [SBIN], connectionEpoch: 1, marketOpen: false })
    await settle()
    expect(reads()).toEqual(['SBIN'])

    page('hidden')
    // A symbol joins the list (a screen fed it), the market opens, the feed
    // signs in again, and the list asks for its quiet rows: each read a quote before.
    view.rerender({ instruments: [SBIN, INFY], connectionEpoch: 1, marketOpen: false })
    await pass(60_000)
    view.rerender({ instruments: [SBIN, INFY], connectionEpoch: 1, marketOpen: true })
    await pass(60_000)
    view.rerender({ instruments: [SBIN, INFY], connectionEpoch: 2, marketOpen: true })
    await pass(60_000)
    act(() => view.result.current.resync(new Set(['NSE:SBIN']), 0))
    await pass(60_000)
    expect(reads()).toEqual(['SBIN'])

    page('visible')
    await settle()
    // One read of the whole list, with what the hidden page owed in it.
    expect(reads()).toEqual(['SBIN', 'SBIN,INFY'])
    expect(view.result.current.quotes.size).toBe(2)
    expect(view.result.current.status).toBe('ready')
  })

  it('reads what a short time away owed, and only that', async () => {
    const view = mount({ instruments: [SBIN], connectionEpoch: 1, marketOpen: true })
    await settle()
    page('hidden')
    // Five seconds away, less than the time that reads everything again.
    view.rerender({ instruments: [SBIN, INFY], connectionEpoch: 1, marketOpen: true })
    await pass(5_000)
    expect(AWAY_MS).toBeGreaterThan(5_000)
    expect(reads()).toEqual(['SBIN'])
    page('visible')
    await settle()
    // SBIN was read five seconds ago and is not read again; INFY is.
    expect(reads()).toEqual(['SBIN', 'INFY'])
  })

  it('reads nothing on a return when nothing was owed and the time away was short', async () => {
    mount({ instruments: [SBIN], connectionEpoch: 1, marketOpen: true })
    await settle()
    page('hidden')
    await pass(5_000)
    page('visible')
    await settle()
    expect(reads()).toEqual(['SBIN'])
  })

  it('opens on a hidden page without a read, and reads when first shown', async () => {
    visibility = 'hidden'
    const view = mount({ instruments: [SBIN], connectionEpoch: 1, marketOpen: true })
    await pass(1_000)
    expect(reads()).toEqual([])
    page('visible')
    await settle()
    expect(reads()).toEqual(['SBIN'])
    expect(view.result.current.status).toBe('ready')
  })
})
