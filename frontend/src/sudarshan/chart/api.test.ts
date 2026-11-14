import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadPreviousOi } from './api'

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
