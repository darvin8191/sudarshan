/**
 * The page against the service's real answers (README, API): the bridge envelope, the codes, the
 * units, and the optional fields. The HTTP client is replaced at the edge of api.ts with the
 * exact shapes the bridge sends; everything above it is the real code.
 */
import { describe, expect, it, vi } from 'vitest'

const http = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))
vi.mock('@/api/client', () => ({ webClient: http }))

import { inRupees, type ScreenerError, screenerApi, traderWords } from './api'
import { mergeResults } from './push'
import { needsWords, warmingNote } from './words'

const ok = (data: unknown) => Promise.resolve({ data: { status: 'success', enabled: true, data } })
const fail = (code: string, message?: string, field?: string) =>
  Promise.resolve({
    data: {
      status: 'error',
      error: { code, ...(message ? { message } : {}), ...(field ? { field } : {}) },
    },
  })

async function problem(request: Promise<unknown>): Promise<ScreenerError> {
  try {
    await request
  } catch (error) {
    return error as ScreenerError
  }
  throw new Error('no error')
}

describe('the envelope', () => {
  it('returns the data of a success', async () => {
    http.get.mockReturnValueOnce(ok({ presets: [] }))
    expect(await screenerApi.presets()).toEqual({ presets: [] })
  })

  it('reads a switched-off screener as an empty, switched-off state with the server words', async () => {
    http.get.mockReturnValueOnce(
      Promise.resolve({
        data: {
          status: 'success',
          enabled: false,
          data: null,
          message: 'The screener is switched off on the server.',
        },
      })
    )
    const state = await screenerApi.state()
    expect(state.enabled).toBe(false)
    expect(state.screens).toEqual([])
    expect(state.engine).toMatchObject({
      state: 'switched_off',
      line: 'The screener is switched off on the server.',
      real_time: false,
    })
  })

  it('refuses another read while switched off with the server sentence, and no code', async () => {
    http.get.mockReturnValueOnce(
      Promise.resolve({
        data: { status: 'success', enabled: false, data: null, message: 'Switched off.' },
      })
    )
    const error = await problem(screenerApi.presets())
    expect(error.message).toBe('Switched off.')
  })

  it('reads an error envelope that came with a success status', async () => {
    http.get.mockReturnValueOnce(fail('not_found', 'This check is no longer in the history.'))
    const error = await problem(screenerApi.run(9))
    expect(error).toMatchObject({
      code: 'gone',
      message: 'This check is no longer in the history.',
    })
  })
})

describe('error codes become sentences for a trader', () => {
  const CODES = [
    ['version_conflict', 'conflict'],
    ['refused', 'invalid'],
    ['not_found', 'gone'],
    ['not_confirmed', 'unanswered'],
    ['unavailable', 'failed'],
  ] as const

  it.each(
    CODES
  )('%s with no sentence of its own reads as words, not a code', async (code, page) => {
    http.get.mockReturnValueOnce(fail(code))
    const error = await problem(screenerApi.presets())
    expect(error.code).toBe(page)
    expect(error.message).toMatch(/ /)
    expect(error.message).not.toMatch(new RegExp(code, 'i'))
    expect(error.message).not.toMatch(/\b[45]\d\d\b|HTTP|envelope|sealed/i)
  })

  it.each(CODES)('%s with a bare code as its sentence is replaced by words', async (code) => {
    http.get.mockReturnValueOnce(fail(code, code))
    const error = await problem(screenerApi.presets())
    expect(error.message).not.toBe(code)
    expect(error.message).toMatch(/ /)
  })

  it('keeps the service sentence when it is one, and the line of the screen it is about', async () => {
    http.post.mockReturnValueOnce(fail('refused', 'Type a number, like 55 or 2500.50.', 'c1.right'))
    const error = await problem(screenerApi.create({ spec: {} as never, request_id: 'r1' }))
    expect(error).toMatchObject({
      code: 'invalid',
      message: 'Type a number, like 55 or 2500.50.',
      field: 'c1.right',
    })
  })

  it('never lets a status number through as a sentence', () => {
    expect(traderWords('failed', 'Request failed with status code 503')).not.toMatch(/503/)
    expect(traderWords('unavailable', null)).toMatch(/not answering/)
  })
})

describe('what is sent', () => {
  it('sends an edit as action edit with the version, and alerts back on as its own action', async () => {
    http.patch.mockReturnValue(ok({ id: 1 }))
    await screenerApi.change(1, { version: 4, action: 'edit', spec: { name: 'x' } as never })
    expect(http.patch.mock.calls[0][1]).toMatchObject({ version: 4, action: 'edit' })
    await screenerApi.change(1, { version: 5, action: 'alerts_on' })
    expect(http.patch.mock.calls[1][1]).toEqual({ version: 5, action: 'alerts_on' })
  })

  it('sends a pin with the row version', async () => {
    http.patch.mockReturnValueOnce(ok({ id: 40 }))
    await screenerApi.member(40, 3, 'pin')
    expect(http.patch.mock.calls.at(-1)?.[1]).toEqual({ action: 'pin', version: 3 })
  })

  it('creates a Paused copy with status and one request id', async () => {
    http.post.mockReturnValueOnce(ok({ id: 9 }))
    await screenerApi.create({ spec: {} as never, request_id: 'abc', status: 'paused' })
    expect(http.post.mock.calls.at(-1)?.[1]).toMatchObject({ status: 'paused', request_id: 'abc' })
  })

  it('deletes with the flag in the body', async () => {
    http.delete.mockReturnValueOnce(ok({ deleted: 3, removed_from_list: 2 }))
    expect(await screenerApi.remove(3, true)).toEqual({ deleted: 3, removed_from_list: 2 })
    expect(http.delete.mock.calls.at(-1)?.[1]).toMatchObject({ data: { remove_added: true } })
  })
})

describe('units', () => {
  const row = (ltp: unknown, change_pct: unknown = 1.5) =>
    ({
      member_id: null,
      key: 'NSE_EQ|INE009A01021',
      symbol: 'INFY',
      exchange: 'NSE',
      rank: 1,
      values: '',
      forming: false,
      chip: null,
      ltp,
      change_pct,
    }) as never

  it('shows a price in rupees: the service sends paise', async () => {
    http.get.mockReturnValueOnce(
      ok({
        screen_id: 1,
        run: null,
        rows: [row(150050), row(null), row('x', 'y')],
        not_checked: { count: 0, reasons: [] },
      })
    )
    const { rows } = await screenerApi.results(1)
    expect(rows[0].ltp).toBe(1500.5)
    expect(rows[0].change_pct).toBe(1.5)
    expect(rows[1].ltp).toBeNull()
    expect(rows[2]).toMatchObject({ ltp: null, change_pct: null })
  })

  it('converts a past run the same way and keeps its note', async () => {
    http.get.mockReturnValueOnce(
      ok({
        screen_id: 1,
        run: { id: 5 },
        rows: [row(2500)],
        not_checked: { count: 0, reasons: [] },
        values_note: 'Values are kept for 7 days; the list for 100 days.',
      })
    )
    const set = await screenerApi.run(5)
    expect(set.rows[0].ltp).toBe(25)
    expect(set.values_note).toMatch(/kept for 7 days/)
  })

  it('leaves the other fields of a row alone', () => {
    const [kept] = inRupees({ rows: [row(100)] }).rows
    expect(kept.key).toBe('NSE_EQ|INE009A01021')
  })
})

describe('a pushed run', () => {
  it('names instruments only: the rest shows as nothing, never as undefined', () => {
    const held = {
      screen_id: 1,
      run: {
        id: 10,
        screen_id: 1,
        at: 1,
        candle: 'a',
        matches: 0,
        entered: 0,
        exited: 0,
        marks: [],
      },
      rows: [],
      not_checked: { count: 0, reasons: [] },
    }
    const next = mergeResults(held, {
      screen_id: 1,
      run_id: 11,
      run_key: 'k',
      at: 2,
      candle: '10:00 to 10:15',
      counts: { matches: 1, entered: 1, exited: 0, missing: 2, not_checked: 3 },
      entered: [{ symbol: 'TCS', exchange: 'NSE', member_id: null } as never],
      exited: [],
      marks: [],
    })
    expect(next.rows[0]).toMatchObject({
      symbol: 'TCS',
      values: '',
      forming: false,
      chip: null,
      ltp: null,
      change_pct: null,
    })
    expect(next.run).toMatchObject({ missing: 2, not_checked: 3, status: 'done' })
  })
})

describe('words for the optional fields', () => {
  it('says what history a screen needs, by timeframe', () => {
    expect(needsWords({ '15': 60, D: 20 })).toBe(
      'Needs 60 candles of 15 min and 20 of day before it can judge every instrument.'
    )
    expect(needsWords({})).toBeNull()
    expect(needsWords(undefined)).toBeNull()
  })

  it('explains a warming-up reason, and only then', () => {
    expect(warmingNote(['Not checked: warming up (37 of 50 candles so far)'])).toMatch(/collecting/)
    expect(warmingNote(['no VWAP yet'])).toBeNull()
  })
})
