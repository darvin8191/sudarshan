import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Chart } from 'openalgo-charts'
import { SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiveBarBuilder } from './liveBars'
import {
  applySessionHours,
  calendarSpec,
  type HolidayRow,
  resetSessionHours,
  type TimingRow,
  withStop,
} from './sessionHours'

/** Epoch milliseconds of an IST wall-clock time, whatever zone the test runs in. */
const ist = (date: string, hh: number, mm: number) => {
  const [y, m, d] = date.split('-').map(Number)
  return Date.UTC(y, m - 1, d, hh, mm) - 19_800_000
}
const secs = (ms: number) => ms / 1000

const TIMINGS: TimingRow[] = [
  { exchange: 'NSE', start_offset: 33_300_000, end_offset: 55_800_000 },
  { exchange: 'BSE', start_offset: 33_300_000, end_offset: 55_800_000 },
  { exchange: 'MCX', start_offset: 32_400_000, end_offset: 86_100_000 },
  { exchange: 'CRYPTO', start_offset: 0, end_offset: 86_399_000 },
]
const CASH = ['NSE', 'BSE', 'NFO', 'BFO', 'CDS', 'BCD']
const HOLIDAYS: HolidayRow[] = [
  {
    date: '2026-10-02',
    holiday_type: 'TRADING_HOLIDAY',
    closed_exchanges: [...CASH, 'MCX'],
    open_exchanges: [],
  },
  {
    date: '2026-10-20',
    holiday_type: 'TRADING_HOLIDAY',
    closed_exchanges: CASH,
    open_exchanges: [
      {
        exchange: 'MCX',
        start_time: ist('2026-10-20', 17, 0),
        end_time: ist('2026-10-20', 23, 55),
      },
    ],
  },
  {
    date: '2026-10-21',
    holiday_type: 'SETTLEMENT_HOLIDAY',
    closed_exchanges: ['NSE'],
    open_exchanges: [],
  },
  {
    date: '2026-11-08',
    holiday_type: 'SPECIAL_SESSION',
    closed_exchanges: [],
    open_exchanges: [
      {
        exchange: 'NSE',
        start_time: ist('2026-11-08', 18, 0),
        end_time: ist('2026-11-08', 19, 15),
      },
      { exchange: 'MCX', start_time: ist('2026-11-08', 18, 0), end_time: ist('2026-11-09', 0, 15) },
    ],
  },
]

describe('calendarSpec', () => {
  it('builds NSE hours from the backend with the pre-open, holidays and special sessions', () => {
    expect(calendarSpec('NSE', TIMINGS, HOLIDAYS)).toEqual({
      timezone: 'Asia/Kolkata',
      sessions: ['0915-1530:23456'],
      preMarketMinutes: 15,
      exceptions: { '2026-10-02': [], '2026-10-20': [], '2026-11-08': ['1800-1915'] },
    })
  })

  it('gives an index the hours of its exchange', () => {
    expect(calendarSpec('NSE_INDEX', TIMINGS, HOLIDAYS)).toEqual(
      calendarSpec('NSE', TIMINGS, HOLIDAYS)
    )
  })

  it('keeps the MCX evening session on a cash holiday and an overnight Muhurat window', () => {
    expect(calendarSpec('MCX', TIMINGS, HOLIDAYS)).toEqual({
      timezone: 'Asia/Kolkata',
      sessions: ['0900-2355:23456'],
      exceptions: { '2026-10-02': [], '2026-10-20': ['1700-2355'], '2026-11-08': ['1800-0015'] },
    })
  })

  it('reads crypto as round the clock with no holidays', () => {
    expect(calendarSpec('CRYPTO', TIMINGS, HOLIDAYS)).toEqual({
      timezone: 'Asia/Kolkata',
      sessions: ['0000-0000'],
      exceptions: {},
    })
  })

  it('has no calendar for an exchange the backend keeps no hours for, or for bad rows', () => {
    expect(calendarSpec('NCDEX', TIMINGS, HOLIDAYS)).toBeNull()
    expect(
      calendarSpec('NSE', [{ exchange: 'NSE', start_offset: 55_800_000, end_offset: 0 }], [])
    ).toBeNull()
  })

  it('answers the market status a trader expects', () => {
    const nse = new SessionCalendar(calendarSpec('NSE', TIMINGS, HOLIDAYS))
    expect(nse.phaseAt(secs(ist('2026-10-01', 9, 5)))).toBe('pre')
    expect(nse.phaseAt(secs(ist('2026-10-01', 10, 0)))).toBe('regular')
    expect(nse.phaseAt(secs(ist('2026-10-01', 16, 0)))).toBe('closed')
    expect(nse.phaseAt(secs(ist('2026-10-02', 10, 0)))).toBe('holiday')
    expect(nse.phaseAt(secs(ist('2026-10-21', 10, 0)))).toBe('regular')
    const mcx = new SessionCalendar(calendarSpec('MCX', TIMINGS, HOLIDAYS))
    expect(mcx.phaseAt(secs(ist('2026-10-20', 18, 0)))).toBe('regular')
    expect(mcx.phaseAt(secs(ist('2026-11-09', 0, 10)))).toBe('regular')
    const crypto = new SessionCalendar(calendarSpec('CRYPTO', TIMINGS, HOLIDAYS))
    expect(crypto.phaseAt(secs(ist('2026-10-04', 3, 0)))).toBe('regular')
  })
})

/**
 * SUDARSHAN: the timings of 7 Dec 2026, from the server's own rows. The vectors file is the
 * contract with sudarshan/regimes.py and OpenAlgo's calendar (test_regimes*.py run it there):
 * every day's close as the chart's calendar reads it, built on a day before the change and on
 * one after it.
 */
interface RegimeDay {
  date: string
  cash: [string, string]
  fo: [string, string]
}
const VECTORS = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'sudarshan', 'tests', 'regimes_vectors.json'), 'utf8')
) as { timings: TimingRow[]; days: RegimeDay[] }
const at = (date: string, hhmmText: string) => {
  const [hh, mm] = hhmmText.split(':').map(Number)
  return ist(date, hh, mm)
}

describe('the timings of 7 Dec 2026 (regimes_vectors.json)', () => {
  const built = ['2026-11-13', '2026-12-04', '2026-12-10', '2027-01-15']

  it.each(built)('every vector day opens and closes on its own clock, built on %s', (day) => {
    expect(VECTORS.days.length).toBeGreaterThanOrEqual(6)
    for (const exchange of ['NFO', 'BFO', 'NSE', 'BSE']) {
      const spec = calendarSpec(exchange, VECTORS.timings, [], day)
      expect(spec).not.toBeNull()
      const calendar = new SessionCalendar(spec)
      const fo = exchange.endsWith('FO')
      // Every vector day is in 2026, inside the years each build covers (January reaches back).
      for (const vector of VECTORS.days) {
        const [opens, closes] = fo ? vector.fo : vector.cash
        const session = calendar.sessionAt(secs(at(vector.date, opens)))
        expect(session, `${exchange} ${vector.date} at ${opens}`).not.toBeNull()
        expect(session?.close, `${exchange} ${vector.date} closes`).toBe(
          secs(at(vector.date, closes))
        )
        expect(calendar.sessionAt(secs(at(vector.date, closes)))).toBeNull()
      }
    }
  })

  it('takes the weekly hours of the day it is built for', () => {
    const before = calendarSpec('NFO', VECTORS.timings, [], '2026-11-13')
    const after = calendarSpec('NFO', VECTORS.timings, [], '2026-12-10')
    expect(before?.sessions).toEqual(['0915-1540:23456'])
    expect(after?.sessions).toEqual(['0915-1545:23456'])
    expect(before?.exceptions?.['2026-12-07']).toEqual(['0915-1545'])
    expect(before?.exceptions?.['2026-12-05']).toBeUndefined() // a Saturday stays closed
    expect(after?.exceptions?.['2026-12-04']).toEqual(['0915-1540'])
    expect(after?.exceptions?.['2026-12-07']).toBeUndefined()
    // Rows without a change are built exactly as before.
    expect(calendarSpec('NSE', VECTORS.timings, [], '2026-12-10')?.exceptions).toEqual({})
  })

  it('keeps a holiday closed and a special session its own window across the change', () => {
    const christmas: HolidayRow = {
      date: '2026-12-25',
      holiday_type: 'TRADING_HOLIDAY',
      closed_exchanges: CASH,
      open_exchanges: [],
    }
    const spec = calendarSpec('NFO', VECTORS.timings, [christmas], '2026-11-13')
    expect(spec?.exceptions?.['2026-12-25']).toEqual([])
    const calendar = new SessionCalendar(spec)
    expect(calendar.sessionAt(secs(at('2026-12-25', '10:00')))).toBeNull()
  })

  it("opens and ends the phone chart's F&O candles on each day's clock", () => {
    const candle = (time: number) => ({
      time,
      open: 100,
      high: 101,
      low: 99,
      close: 100,
      volume: 0,
    })
    for (const [date, close, next] of [
      ['2026-12-04', '15:40', '2026-12-07'],
      ['2026-12-08', '15:45', '2026-12-09'], // a NIFTY expiry: its contract stops at 15:30
      ['2026-12-10', '15:45', '2026-12-11'],
    ]) {
      const calendar = new SessionCalendar(calendarSpec('NFO', VECTORS.timings, [], date))
      const fives = new LiveBarBuilder({ interval: '5m', session: calendar })
      fives.seed(candle(secs(at(date, '15:35'))))
      const opened = fives.onClock(secs(at(date, '15:40')) + 0.5)
      if (close === '15:45') {
        expect(opened?.bar.time, `${date}: the 15:40 candle opens on time`).toBe(
          secs(at(date, '15:40'))
        )
        // That candle is the day's last: the clock next looks at the next day's open.
        expect(fives.nextClockAt(secs(at(date, '15:41')))).toBe(secs(at(next, '09:15')))
      } else {
        expect(opened, `${date}: nothing opens after the close`).toBeNull()
      }
      const hourly = new LiveBarBuilder({ interval: '1h', session: calendar })
      hourly.seed(candle(secs(at(date, '14:15'))))
      expect(hourly.countdown(secs(at(date, '15:20')))).toEqual({
        kind: 'closes',
        at: secs(at(date, close)),
      })
    }
  })

  it('ignores a change that cannot be read and still builds the hours', () => {
    const rows: TimingRow[] = [
      {
        exchange: 'NFO',
        start_offset: 33_300_000,
        end_offset: 56_400_000,
        changes: [
          { from: 'soon', end_offset: 56_700_000 },
          { from: '2026-12-07', end_offset: 99_000_000 },
        ],
      },
    ]
    expect(calendarSpec('NFO', rows, [], '2026-12-10')).toEqual({
      timezone: 'Asia/Kolkata',
      sessions: ['0915-1540:23456'],
      exceptions: {},
    })
  })
})

describe('applySessionHours', () => {
  const fetchMock = vi.fn()
  const chart = () =>
    ({ isDestroyed: false, setSessionCalendar: vi.fn() }) as unknown as Chart & {
      setSessionCalendar: ReturnType<typeof vi.fn>
    }
  const answer = (data: unknown) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'success', data }),
    })
  const settle = async () => {
    for (let i = 0; i < 20; i++) await Promise.resolve()
  }

  beforeEach(() => {
    resetSessionHours()
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    vi.setSystemTime(new Date('2026-10-01T05:00:00Z'))
    fetchMock.mockReset()
    fetchMock.mockImplementation((url: string) =>
      answer(url.includes('timings') ? TIMINGS : HOLIDAYS)
    )
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('fetches once for every pane and hands each chart its exchange hours', async () => {
    const a = chart()
    const b = chart()
    applySessionHours(a, 'NSE', 'key')
    applySessionHours(b, 'MCX', 'key')
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [, holidays] = fetchMock.mock.calls.map((call) => call[0])
    expect(holidays).toBe('/api/v1/market/holidays')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ apikey: 'key', year: 2026 })
    expect(a.setSessionCalendar).toHaveBeenCalledTimes(1)
    expect(b.setSessionCalendar).toHaveBeenCalledTimes(1)
    // A later build the same day is answered from memory.
    const c = chart()
    applySessionHours(c, 'NSE', 'key')
    expect(c.setSessionCalendar).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('fetches again when the IST day turns, whatever the browser zone', async () => {
    vi.setSystemTime(new Date('2026-09-30T18:00:00Z')) // 23:30 IST
    applySessionHours(chart(), 'NSE', 'key')
    await settle()
    vi.setSystemTime(new Date('2026-09-30T18:40:00Z')) // 00:10 IST the next day
    applySessionHours(chart(), 'NSE', 'key')
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('leaves the chart without hours when a request fails, and retries only after a pause', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError('offline')))
    const a = chart()
    applySessionHours(a, 'NSE', 'key')
    await settle()
    expect(a.setSessionCalendar).not.toHaveBeenCalled()
    applySessionHours(chart(), 'NSE', 'key')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    vi.setSystemTime(new Date('2026-10-01T05:06:00Z'))
    fetchMock.mockImplementation((url: string) =>
      answer(url.includes('timings') ? TIMINGS : HOLIDAYS)
    )
    const b = chart()
    applySessionHours(b, 'NSE', 'key')
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(4)
    expect(b.setSessionCalendar).toHaveBeenCalledTimes(1)
  })

  it('gives up on a request that does not answer', async () => {
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener('abort', () => reject(new Error('aborted')))
        )
    )
    const a = chart()
    applySessionHours(a, 'NSE', 'key')
    vi.advanceTimersByTime(8_000)
    await settle()
    expect(a.setSessionCalendar).not.toHaveBeenCalled()
  })

  it('does not touch a chart that was replaced before the hours arrived', async () => {
    const a = chart()
    applySessionHours(a, 'NSE', 'key')
    ;(a as { isDestroyed: boolean }).isDestroyed = true
    await settle()
    expect(a.setSessionCalendar).not.toHaveBeenCalled()
  })
})

/**
 * SUDARSHAN review M3: an expiring index contract stops at 15:30 on its expiry
 * day from 7 Dec 2026 while F&O trades to 15:45 (15:40 for every contract
 * before). The server sends the stop (regimes_vectors.json `expiries`); the
 * contract's calendar ends that day at it.
 */
describe("an expiring contract's own stop (review M3)", () => {
  interface Expiry {
    symbol: string
    exchange: string
    expiry: string
    index: boolean
    stop: string
  }
  const EXPIRIES = (
    JSON.parse(
      readFileSync(join(process.cwd(), '..', 'sudarshan', 'tests', 'regimes_vectors.json'), 'utf8')
    ) as { expiries: Expiry[] }
  ).expiries
  const candle = (time: number) => ({ time, open: 100, high: 101, low: 99, close: 100, volume: 0 })

  it.each(EXPIRIES)('$symbol stops at $stop on $expiry', (contract) => {
    const spec = calendarSpec(contract.exchange, VECTORS.timings, [], contract.expiry)
    expect(spec).not.toBeNull()
    const stopsAt = at(contract.expiry, contract.stop)
    const calendar = new SessionCalendar(withStop(spec as NonNullable<typeof spec>, stopsAt))
    const stop = secs(stopsAt)
    expect(calendar.sessionAt(stop - 1)?.close).toBe(stop)
    expect(calendar.sessionAt(stop)).toBeNull()
    // The next day trades as the exchange does.
    const next = new Date(`${contract.expiry}T00:00:00Z`)
    next.setUTCDate(next.getUTCDate() + (next.getUTCDay() === 5 ? 3 : 1))
    const day = next.toISOString().slice(0, 10)
    expect(calendar.sessionAt(secs(at(day, '10:00')))).not.toBeNull()
  })

  it('Tue 8 Dec: no candle after 15:30, the hourly countdown ends at it, the day after is whole', () => {
    const date = '2026-12-08'
    const spec = calendarSpec('NFO', VECTORS.timings, [], date) as NonNullable<
      ReturnType<typeof calendarSpec>
    >
    const calendar = new SessionCalendar(withStop(spec, at(date, '15:30')))
    const fives = new LiveBarBuilder({ interval: '5m', session: calendar })
    fives.seed(candle(secs(at(date, '15:25'))))
    expect(fives.onClock(secs(at(date, '15:30')) + 0.5)).toBeNull()
    expect(fives.nextClockAt(secs(at(date, '15:31')))).toBe(secs(at('2026-12-09', '09:15')))
    const hourly = new LiveBarBuilder({ interval: '1h', session: calendar })
    hourly.seed(candle(secs(at(date, '14:15'))))
    expect(hourly.countdown(secs(at(date, '15:20')))).toEqual({
      kind: 'closes',
      at: secs(at(date, '15:30')),
    })
    // The exchange's own calendar runs on to 15:45 that day: the 15:30 candle opens there.
    const plain = new LiveBarBuilder({ interval: '5m', session: new SessionCalendar(spec) })
    plain.seed(candle(secs(at(date, '15:25'))))
    expect(plain.onClock(secs(at(date, '15:30')) + 0.5)?.bar.time).toBe(secs(at(date, '15:30')))
  })

  it('keeps a holiday closed and a stop at the close changes nothing', () => {
    const spec = calendarSpec('NFO', VECTORS.timings, [], '2026-12-10') as NonNullable<
      ReturnType<typeof calendarSpec>
    >
    expect(withStop(spec, at('2026-12-12', '15:30')).exceptions?.['2026-12-12']).toEqual([])
    expect(withStop(spec, at('2026-12-29', '15:45')).exceptions?.['2026-12-29']).toEqual([
      '0915-1545',
    ])
    expect(withStop(spec, at('2026-12-29', '15:30')).exceptions?.['2026-12-29']).toEqual([
      '0915-1530',
    ])
  })
})
