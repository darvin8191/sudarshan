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
    expect(onRefused).toHaveBeenCalledWith(['NSE:NOSUCHCO'])
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
