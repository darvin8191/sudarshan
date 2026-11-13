import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  instrumentSessionFor,
  readInstrumentSession,
  resetInstrumentSessions,
} from './instrumentSession'

/** Epoch ms of a time in India on Thursday 10 December 2026. */
const dec10 = (h: number, mi: number) => Date.UTC(2026, 11, 10, h, mi) - 19_800_000

const answer = (data: unknown, ok = true) =>
  Promise.resolve({
    ok,
    status: ok ? 200 : 503,
    json: () => Promise.resolve(ok ? { status: 'success', data } : { status: 'error' }),
  })

describe('an instrument session facts (review M1, M2, M3)', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    resetInstrumentSessions()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('reads the closing window in chart seconds and the stop in epoch ms', () => {
    expect(
      readInstrumentSession({
        day: '2026-12-10',
        stops_at: null,
        closing: { from: dec10(15, 30), until: dec10(15, 45) },
      })
    ).toEqual({
      day: '2026-12-10',
      stopsAt: null,
      closing: { from: dec10(15, 30) / 1000, until: dec10(15, 45) / 1000 },
    })
    expect(readInstrumentSession({ day: '2026-12-08', stops_at: 1_765_188_000_000 })).toEqual({
      day: '2026-12-08',
      stopsAt: 1_765_188_000_000,
      closing: null,
    })
    expect(readInstrumentSession(null)).toBeNull()
    expect(readInstrumentSession({ stops_at: 1 })).toBeNull()
    expect(
      readInstrumentSession({ day: 'd', closing: { from: dec10(15, 45), until: dec10(15, 30) } })
        ?.closing
    ).toBeNull()
  })

  it('asks once per instrument per day, from the server', async () => {
    fetchMock.mockReturnValue(answer({ day: '2026-12-10', stops_at: null, closing: null }))
    const now = dec10(11, 0)
    await instrumentSessionFor('RELIANCE', 'NSE', now)
    await instrumentSessionFor('RELIANCE', 'NSE', now + 60_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/sudarshan/session/instrument?symbol=RELIANCE&exchange=NSE'
    )
    await instrumentSessionFor('RELIANCE', 'NSE', now + 86_400_000) // the next day
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('leaves the chart on the exchange hours when the server cannot say, and asks again later', async () => {
    fetchMock.mockReturnValueOnce(answer(null, false))
    const now = dec10(11, 0)
    expect(await instrumentSessionFor('NIFTY', 'NSE_INDEX', now)).toBeNull()
    expect(await instrumentSessionFor('NIFTY', 'NSE_INDEX', now + 60_000)).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockReturnValueOnce(answer({ day: '2026-12-10', stops_at: null, closing: null }))
    expect(await instrumentSessionFor('NIFTY', 'NSE_INDEX', now + 5 * 60_000)).not.toBeNull()
    expect(await instrumentSessionFor('', 'NSE')).toBeNull()
  })
})
