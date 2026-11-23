/**
 * M8.2 TD1: an expired contract in a list is never read, and a read the server refuses only for
 * expired contracts (its master no longer lists them) is not a failure. The server's refusal is
 * the shape `services/quotes_service.get_multiquotes_with_auth` answers when it lists none of a
 * call's symbols: 400 with `invalid_symbols`. Every name is made up.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args) },
}))

import { type Instrument, useQuoteSnapshot } from './useQuoteSnapshot'

const NOW = Date.parse('2026-11-24T11:20:00+05:30')
const SBIN = { symbol: 'SBIN', exchange: 'NSE' }
/** Expired three weeks ago: never read. */
const GONE = { symbol: 'NIFTY03NOV2622700PE', exchange: 'NFO' }
/** Expires today: read, until the server says its master no longer lists it. */
const TODAY = { symbol: 'NIFTY24NOV2625900CE', exchange: 'NFO' }
/** A share the master does not list: not a contract, so its refusal is still a failure. */
const ODD = { symbol: 'NOSUCHCO', exchange: 'NSE' }
const UNLISTED = new Set(['NFO:NIFTY24NOV2625900CE', 'NSE:NOSUCHCO', 'NFO:NIFTY03NOV2622700PE'])

/** The server: a quote for what its master lists, 400 when it lists none of the call. */
async function server(_key: string, asked: Instrument[]) {
  const listed = asked.filter((each) => !UNLISTED.has(`${each.exchange}:${each.symbol}`))
  if (listed.length === 0)
    throw Object.assign(new Error('Request failed with status code 400'), {
      response: {
        status: 400,
        data: {
          status: 'error',
          message: `Symbol '${asked[0].symbol}' not found for exchange '${asked[0].exchange}'.`,
          invalid_symbols: asked.map((each) => ({ ...each, error: 'not found' })),
        },
      },
    })
  return {
    status: 'success',
    results: asked.map((each) =>
      listed.includes(each)
        ? { ...each, data: { ltp: 100, prev_close: 99 } }
        : { ...each, error: 'not found' }
    ),
  }
}

const settle = () => act(() => vi.advanceTimersByTimeAsync(0))
const pass = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms))
const reads = () =>
  getMultiQuotes.mock.calls.map(([, asked]) =>
    (asked as Instrument[]).map((each) => each.symbol).join(',')
  )
const mount = (instruments: Instrument[], onRefused = vi.fn()) => ({
  onRefused,
  view: renderHook(() =>
    useQuoteSnapshot({
      apiKey: 'key',
      instruments,
      connectionEpoch: 1,
      marketOpen: true,
      onRefused,
    })
  ),
})

beforeEach(() => {
  vi.useFakeTimers({ now: NOW })
  getMultiQuotes.mockReset()
  getMultiQuotes.mockImplementation(server)
})
afterEach(() => {
  vi.useRealTimers()
})

describe('expired contracts in the quote snapshot (M8.2 TD1)', () => {
  it('never reads a contract whose day is behind today, whatever asks', async () => {
    const { view } = mount([SBIN, GONE])
    await settle()
    await pass(1_000)
    act(() => view.result.current.resync(new Set(['NFO:NIFTY03NOV2622700PE']), 0))
    await settle()
    act(() => view.result.current.retry())
    await settle()
    expect(reads()).toEqual(['SBIN', 'SBIN'])
    expect(view.result.current.status).toBe('ready')
  })

  it('takes a refusal of only expired contracts as no failure, says which, and never asks again', async () => {
    const { view, onRefused } = mount([SBIN, TODAY])
    await settle()
    expect(reads()).toEqual(['SBIN,NIFTY24NOV2625900CE'])
    // The quiet re-sync asks for the unpriced contract alone: the server lists none of the call.
    act(() => view.result.current.resync(new Set(['NFO:NIFTY24NOV2625900CE']), 0))
    await settle()
    expect(reads()).toHaveLength(2)
    expect(view.result.current.status).toBe('ready')
    expect(onRefused).toHaveBeenCalledWith(['NFO:NIFTY24NOV2625900CE'])
    await pass(1_000)
    act(() => view.result.current.resync(new Set(['NFO:NIFTY24NOV2625900CE']), 0))
    await settle()
    act(() => view.result.current.retry())
    await settle()
    let all: unknown = 'not read'
    await act(async () => {
      all = await view.result.current.readAll()
    })
    expect(reads().slice(2)).toEqual(['SBIN', 'SBIN'])
    expect(all).toBeInstanceOf(Map)
    expect(view.result.current.status).toBe('ready')
  })

  it('still fails a read the server refuses for anything that is not an expired contract', async () => {
    const { view, onRefused } = mount([SBIN, ODD])
    await settle()
    act(() => view.result.current.resync(new Set(['NSE:NOSUCHCO']), 0))
    await settle()
    expect(view.result.current.status).toBe('error')
    // Review M2: such a refusal marks nothing gone.
    expect(onRefused).not.toHaveBeenCalled()
    act(() => view.result.current.resync(new Set(['NSE:NOSUCHCO']), 0))
    await settle()
    expect(reads()).toEqual(['SBIN,NOSUCHCO', 'NOSUCHCO', 'NOSUCHCO'])
  })

  it('still fails a read that fails any other way', async () => {
    getMultiQuotes.mockRejectedValueOnce(
      Object.assign(new Error('Request failed with status code 400'), {
        response: { status: 400, data: { status: 'error', message: 'Invalid request.' } },
      })
    )
    const { view } = mount([SBIN])
    await settle()
    expect(view.result.current.status).toBe('error')
  })
})

/**
 * v0.17.0 phone review M2: the morning login deletes the server's contract list and loads it
 * again (`master_contract_db.py`), and in between the server refuses every call, as it does a
 * call of expired contracts. 24 Nov 2026 is a NIFTY expiry; at 09:05 the list is not back yet.
 */
describe('a read while the contract list is being replaced (M2)', () => {
  const MORNING = Date.parse('2026-11-24T09:05:00+05:30')
  const KEY = 'NFO:NIFTY24NOV2625900CE'
  const state = { listless: true }
  /** The server during the download: it lists nothing, so every call is refused whole. */
  async function downloading(key: string, asked: Instrument[]) {
    if (!state.listless) return server(key, asked)
    throw Object.assign(new Error('Request failed with status code 400'), {
      response: {
        status: 400,
        data: {
          status: 'error',
          message: `Symbol '${asked[0].symbol}' not found for exchange '${asked[0].exchange}'.`,
          invalid_symbols: asked.map((each) => ({ ...each, error: 'not found' })),
        },
      },
    })
  }
  const mountListed = (instruments: Instrument[]) => {
    const onListed = vi.fn()
    const onRefused = vi.fn()
    const view = renderHook(() =>
      useQuoteSnapshot({
        apiKey: 'key',
        instruments,
        connectionEpoch: 1,
        marketOpen: false,
        onRefused,
        onListed,
      })
    )
    return { view, onRefused, onListed }
  }

  beforeEach(() => {
    vi.setSystemTime(MORNING)
    state.listless = true
    UNLISTED.delete(KEY) // today's contract trades all day once the list is back
    getMultiQuotes.mockImplementation(downloading)
  })
  afterEach(() => {
    UNLISTED.add(KEY)
  })

  it("takes a refusal naming a share with today's contract as a failed read, and reads both once the list is back (the review's probe)", async () => {
    const { view, onRefused } = mountListed([SBIN, TODAY])
    await settle()
    expect(view.result.current.status).toBe('error')
    expect(onRefused).not.toHaveBeenCalled()
    state.listless = false
    await pass(20_000)
    act(() => view.result.current.retry())
    await settle()
    expect(reads()).toEqual(['SBIN,NIFTY24NOV2625900CE', 'SBIN,NIFTY24NOV2625900CE'])
    expect(view.result.current.status).toBe('ready')
    expect(view.result.current.quotes.get(KEY)?.quote.ltp).toBe(100)
  })

  it('asks a contract refused alone on its expiry day again when told, and lists it once it is quoted', async () => {
    const { view, onRefused, onListed } = mountListed([TODAY])
    await settle()
    expect(onRefused).toHaveBeenCalledWith([KEY])
    act(() => view.result.current.retry())
    await settle()
    expect(reads()).toHaveLength(1) // gone: no read asks for it
    // Asked again while the list is still loading: refused again, still gone.
    act(() => view.result.current.reask(new Set([KEY])))
    await settle()
    expect(reads()).toHaveLength(2)
    expect(onListed).not.toHaveBeenCalled()
    act(() => view.result.current.retry())
    await settle()
    expect(reads()).toHaveLength(2)
    // The list is back: asked again, quoted, listed, and read like any other row from then on.
    state.listless = false
    act(() => view.result.current.reask(new Set([KEY])))
    await settle()
    expect(onListed).toHaveBeenCalledWith([KEY])
    expect(view.result.current.quotes.get(KEY)?.quote.ltp).toBe(100)
    await pass(1_000)
    act(() => view.result.current.retry())
    await settle()
    expect(reads()).toEqual([
      'NIFTY24NOV2625900CE',
      'NIFTY24NOV2625900CE',
      'NIFTY24NOV2625900CE',
      'NIFTY24NOV2625900CE',
    ])
  })

  it('keeps it gone when the read asking again fails another way, and asks only for what is refused', async () => {
    const { view, onListed } = mountListed([SBIN, TODAY])
    // SBIN is listed again first; then today's contract is asked alone and refused.
    state.listless = false
    UNLISTED.add(KEY)
    await settle()
    act(() => view.result.current.resync(new Set([KEY]), 0))
    await settle()
    getMultiQuotes.mockRejectedValueOnce(new Error('Network Error'))
    act(() => view.result.current.reask(new Set([KEY, 'NSE:SBIN'])))
    await settle()
    expect(reads().slice(-1)).toEqual(['NIFTY24NOV2625900CE'])
    expect(onListed).not.toHaveBeenCalled()
    act(() => view.result.current.retry())
    await settle()
    expect(reads().slice(-1)).toEqual(['SBIN'])
  })
})
