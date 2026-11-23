import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadExpiries, loadInstrument, loadPreviousOi } from './api'

/** Epoch seconds of midnight in India on a day of October 2026, as daily history stamps it. */
const day = (d: number) => Date.UTC(2026, 9, d) / 1000 - 19_800
const now = Date.UTC(2026, 9, 20, 4, 30) // 10:00 in India, Tuesday 20 October

function answer(rows: unknown) {
  const fetchMock = vi.fn(async (_url: string, init?: { body?: string }) => ({
    ok: true,
    json: async () => ({ status: 'success', data: rows, asked: init?.body }),
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("yesterday's open interest (C9)", () => {
  it('takes the last daily close before today, asking daily history once for ten days', async () => {
    const fetchMock = answer([
      { timestamp: day(16), oi: 1_100_000 },
      { timestamp: day(19), oi: 1_150_000 },
      { timestamp: day(20), oi: 1_201_300 },
    ])
    expect(await loadPreviousOi('key', 'NIFTY27OCT2625900CE', 'NFO', now)).toBe(1_150_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body).toMatchObject({
      symbol: 'NIFTY27OCT2625900CE',
      exchange: 'NFO',
      interval: 'D',
      start_date: '2026-10-10',
      end_date: '2026-10-20',
    })
  })

  it('says it does not know when history carries no open interest, or cannot be read', async () => {
    answer([{ timestamp: day(19), oi: 0 }, { timestamp: day(16) }])
    expect(await loadPreviousOi('key', 'SBIN', 'NSE', now)).toBeNull()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('offline')))
    )
    expect(await loadPreviousOi('key', 'SBIN', 'NSE', now)).toBeNull()
  })
})

describe("the master's answer for an instrument (TD2)", () => {
  const reply = (status: number, body: unknown) =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: status < 400, status, json: async () => body }))
    )

  it('reads the ticket facts of a listed contract', async () => {
    reply(200, { status: 'success', data: { name: 'NIFTY', lotsize: 65, tick_size: 0.05 } })
    expect(await loadInstrument('key', 'NIFTY24NOV2625000CE', 'NFO')).toEqual({
      name: 'NIFTY',
      lotSize: 65,
      tickSize: 0.05,
    })
  })

  it('tells a contract the master does not list from one that could not be read', async () => {
    reply(404, { status: 'error', message: 'Symbol NIFTY17NOV2625000CE not found in exchange NFO' })
    expect(await loadInstrument('key', 'NIFTY17NOV2625000CE', 'NFO')).toBe('unlisted')
    reply(500, { status: 'error', message: 'An error occurred' })
    expect(await loadInstrument('key', 'NIFTY17NOV2625000CE', 'NFO')).toBeNull()
    reply(403, { status: 'error', message: 'Invalid openalgo apikey' })
    expect(await loadInstrument('key', 'NIFTY17NOV2625000CE', 'NFO')).toBeNull()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('offline')))
    )
    expect(await loadInstrument('key', 'NIFTY17NOV2625000CE', 'NFO')).toBeNull()
  })

  it("reads the expiry list of an underlying's options or futures, once", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ status: 'success', data: ['24-NOV-26', '01-DEC-26'] }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await loadExpiries('key', 'NIFTY', 'NFO', 'options')).toEqual(['24-NOV-26', '01-DEC-26'])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]).toEqual([
      '/api/v1/expiry',
      expect.objectContaining({
        body: JSON.stringify({
          apikey: 'key',
          symbol: 'NIFTY',
          exchange: 'NFO',
          instrumenttype: 'options',
        }),
      }),
    ])
    reply(500, { status: 'error' })
    expect(await loadExpiries('key', 'NIFTY', 'NFO', 'futures')).toBeNull()
  })
})
