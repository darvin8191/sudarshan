/**
 * A trade from the journal on the phone chart (M14.8, design p7 9.7): the interval chosen from
 * the trade's length, the candle each fill lands in, the marks (shape, letter, price, side) and
 * the view, as vectors; then the hook that reads the trade and drives the chart, on a fake chart.
 * Every value is synthetic.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/sudarshan/journal/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/journal/api')>()),
  journalCall: (...args: unknown[]) => call(...args),
}))

/** The app's one Socket.IO connection, as far as the hook uses it. */
const socket = vi.hoisted(() => {
  const heard = new Map<string, Set<(frame?: unknown) => void>>()
  return {
    connected: true,
    heard,
    on(event: string, fn: (frame?: unknown) => void) {
      heard.set(event, (heard.get(event) ?? new Set()).add(fn))
    },
    off(event: string, fn: (frame?: unknown) => void) {
      heard.get(event)?.delete(fn)
    },
    emit(event: string, frame?: unknown) {
      for (const fn of [...(heard.get(event) ?? [])]) fn(frame)
    },
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket, playAlertSound: () => {} }),
}))

import { JournalError } from '@/sudarshan/journal/api'
import { AFTER_ORDER_MS, AWAY_MS } from '@/sudarshan/journal/useJournalSync'
import {
  barIndexOf,
  FOCUS_ENTRY_BARS,
  FOCUS_MAX_BARS,
  FOCUS_MIN_BARS,
  focusRange,
  frameIsFor,
  type MarkLeg,
  marksAsOf,
  NOT_HELD,
  NOT_SHOWN,
  OLDER_THAN_HISTORY,
  outsideWords,
  PICK_LONGER,
  SHOWN_DAILY,
  type TripChart,
  tripChartPath,
  tripIdOf,
  tripInterval,
  tripMarkers,
  useTripMarkers,
} from './tripMarkers'

/** Epoch ms of a time in India in November 2026. */
const ist = (day: number, h: number, m: number, s = 0) =>
  Date.UTC(2026, 10, day, h, m, s) - 19_800_000
const sec = (ms: number) => ms / 1000
const MIN = 60_000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

/** A session's candles: every `step` seconds from 09:15 to 15:29 on each day. */
function session(days: number[], step: number): number[] {
  const out: number[] = []
  for (const day of days)
    for (let t = sec(ist(day, 9, 15)); t < sec(ist(day, 15, 30)); t += step) out.push(t)
  return out
}

describe('the interval comes from the trade length (vectors)', () => {
  const open = ist(16, 10, 0)
  it.each([
    ['a 20-minute scalp', 20 * MIN, '1m'],
    ['exactly two hours', 2 * HOUR, '1m'],
    ['two hours and a second', 2 * HOUR + 1000, '5m'],
    ['a day', DAY, '5m'],
    ['exactly two days', 2 * DAY, '5m'],
    ['two days and a minute', 2 * DAY + MIN, 'D'],
    ['three weeks', 21 * DAY, 'D'],
    ['no time at all', 0, '1m'],
  ])('%s: %s', (_what, length, want) => {
    expect(tripInterval(open, open + length, open + 30 * DAY)).toBe(want)
  })

  it('an open trade runs to now', () => {
    expect(tripInterval(open, null, open + 90 * MIN)).toBe('1m')
    expect(tripInterval(open, null, open + 5 * HOUR)).toBe('5m')
    expect(tripInterval(open, null, open + 3 * DAY)).toBe('D')
  })
})

describe('the address', () => {
  it('names the trade on the chart address of its instrument', () => {
    expect(tripChartPath({ id: 7, symbol: 'SBIN', exchange: 'NSE' })).toBe('/chart/NSE/SBIN?trip=7')
    expect(tripChartPath({ id: 42, symbol: 'M&M', exchange: 'NSE' })).toBe(
      '/chart/NSE/M%26M?trip=42'
    )
  })

  it.each([
    ['?trip=7', 7],
    ['?trip=7&x=1', 7],
    ['?trip=0', null],
    ['?trip=-3', null],
    ['?trip=7.5', null],
    ['?trip=abc', null],
    ['?trip=', null],
    ['', null],
    ['?trip=1234567890123456', null],
  ])('reads %j as %j', (search, want) => {
    expect(tripIdOf(search)).toBe(want)
  })
})

describe('the candle a fill lands in (vectors)', () => {
  const minutes = session([16, 17], 60)
  const fives = session([16, 17], 300)
  it.each([
    ['at a candle open', minutes, ist(16, 10, 2), false, '16 10:02'],
    ['inside a minute', minutes, ist(16, 10, 2, 41), false, '16 10:02'],
    ['inside five minutes', fives, ist(16, 10, 7, 30), false, '16 10:05'],
    ['the last candle of a day', fives, ist(16, 15, 29, 59), false, '16 15:25'],
    ['a pre-open fill goes to its own day', fives, ist(17, 9, 7, 30), false, '17 09:15'],
    ['a pre-open fill on the first day', fives, ist(16, 9, 8), false, '16 09:15'],
  ])('%s', (_what, times, at, daily, want) => {
    const index = barIndexOf(times, sec(at), daily)
    const day = new Date(times[index] * 1000 + 19_800_000)
    const label = `${day.getUTCDate()} ${String(day.getUTCHours()).padStart(2, '0')}:${String(day.getUTCMinutes()).padStart(2, '0')}`
    expect(label).toBe(want)
  })

  it('a fill before the first candle is out of the history', () => {
    expect(barIndexOf(fives, sec(ist(13, 14, 0)), false)).toBe(-1)
    expect(barIndexOf([], sec(ist(16, 10, 0)), false)).toBe(-1)
  })

  it('history that starts later in the day leaves an earlier fill of that day out, not pulled in', () => {
    const late = fives.slice(20) // from 10:55 on the 16th
    expect(barIndexOf(late, sec(ist(16, 10, 2)), false)).toBe(-1)
    expect(barIndexOf(late, sec(ist(16, 10, 41)), false)).toBe(0) // 14 minutes before: the pre-open bound
    expect(barIndexOf(late, sec(ist(16, 10, 39, 59)), false)).toBe(-1)
  })

  it.each([
    ['midnight in India', 0, 0],
    ['the session open', 9, 15],
    ['midnight UTC', 5, 30],
  ])('daily candles anchored at %s take a fill of their day', (_what, h, m) => {
    const days = [12, 13, 16, 17, 18].map((d) => sec(ist(d, h, m)))
    expect(barIndexOf(days, sec(ist(16, 9, 7)), true)).toBe(2)
    expect(barIndexOf(days, sec(ist(16, 15, 29)), true)).toBe(2)
    // Review L3: a day the history skips is never marked on the day before.
    expect(barIndexOf(days, sec(ist(14, 11, 0)), true)).toBe(NOT_HELD)
    expect(barIndexOf(days, sec(ist(19, 11, 0)), true)).toBe(NOT_HELD)
    // Candles of a week hold many days: the last one at or before the fill is its own.
    expect(barIndexOf(days, sec(ist(14, 11, 0)), true, true)).toBe(1)
    expect(barIndexOf(days, sec(ist(11, 11, 0)), true)).toBe(-1)
  })
})

const leg = (
  role: MarkLeg['role'],
  action: MarkLeg['action'],
  qty: number,
  price: number,
  at: number
): MarkLeg => ({ role, action, qty, price_u: Math.round(price * 10_000), exch_ts_ms: at })

describe('the marks (vectors)', () => {
  const minutes = session([16], 60)
  const at = (t: number) => minutes.indexOf(sec(t))

  it('a long trade: B with its price under the entry, S over the exit, as tags', () => {
    const marks = tripMarkers(
      [
        leg('entry', 'BUY', 65, 131.2, ist(16, 10, 2, 5)),
        leg('exit', 'SELL', 65, 138.45, ist(16, 10, 41, 50)),
      ],
      minutes,
      false,
      2
    )
    expect(marks.markers).toEqual([
      {
        id: `trip:entry:BUY:${sec(ist(16, 10, 2))}`,
        time: sec(ist(16, 10, 2)),
        side: 'BUY',
        shape: 'tag',
        text: 'B 131.20',
        short: 'B',
      },
      {
        id: `trip:exit:SELL:${sec(ist(16, 10, 41))}`,
        time: sec(ist(16, 10, 41)),
        side: 'SELL',
        shape: 'tag',
        text: 'S',
        short: 'S',
      },
    ])
    expect(marks).toMatchObject({
      first: at(ist(16, 10, 2)),
      last: at(ist(16, 10, 41)),
      older: false,
    })
  })

  it('a short trade: the side decides the look, so its entry is an S with the price and its exit a B', () => {
    const marks = tripMarkers(
      [
        leg('entry', 'SELL', 30, 512.5, ist(16, 11, 0, 9)),
        leg('exit', 'BUY', 30, 498.05, ist(16, 11, 30)),
      ],
      minutes,
      false,
      2
    )
    expect(marks.markers.map((m) => [m.side, m.shape, m.text])).toEqual([
      ['SELL', 'tag', 'S 512.50'],
      ['BUY', 'tag', 'B'],
    ])
  })

  it('fills of one candle, role and side are one mark at their average; others stay apart', () => {
    const marks = tripMarkers(
      [
        leg('entry', 'BUY', 65, 100, ist(16, 10, 0, 1)),
        leg('entry', 'BUY', 130, 101.5, ist(16, 10, 0, 40)),
        leg('entry', 'BUY', 65, 99, ist(16, 10, 3)),
        leg('exit', 'SELL', 130, 105, ist(16, 10, 20)),
        leg('exit', 'SELL', 130, 104.95, ist(16, 10, 20, 30)),
      ],
      minutes,
      false,
      2
    )
    // (65 x 100 + 130 x 101.5) / 195 = 101.00
    expect(marks.markers.map((m) => m.text)).toEqual(['B 101.00', 'B 99.00', 'S'])
    expect(marks.markers).toHaveLength(3)
  })

  it('writes the price in the instrument decimals and with Indian grouping', () => {
    const marks = tripMarkers([leg('entry', 'BUY', 65, 25874.3, ist(16, 10, 0))], minutes, false, 2)
    expect(marks.markers[0].text).toBe('B 25,874.30')
    const four = tripMarkers([leg('entry', 'BUY', 1, 83.1225, ist(16, 10, 0))], minutes, false, 4)
    expect(four.markers[0].text).toBe('B 83.1225')
  })

  it('an entry before the first candle is older than the history; a later exit is still marked', () => {
    const marks = tripMarkers(
      [
        leg('entry', 'BUY', 65, 131.2, ist(13, 14, 0)),
        leg('exit', 'SELL', 65, 140, ist(16, 10, 5)),
      ],
      minutes,
      false,
      2
    )
    expect(marks.older).toBe(true)
    expect(marks.first).toBeNull()
    expect(marks.markers.map((m) => m.text)).toEqual(['S'])
  })

  it('a trade the journal saw no entry of is placed by its first leg', () => {
    const marks = tripMarkers([leg('exit', 'SELL', 65, 140, ist(16, 10, 5))], minutes, false, 2)
    expect(marks).toMatchObject({ first: at(ist(16, 10, 5)), older: false })
  })

  it('no legs, no marks', () => {
    expect(tripMarkers([], minutes, false, 2)).toEqual({
      markers: [],
      first: null,
      last: null,
      older: false,
      outside: 0,
      fills: new Map(),
    })
  })

  // Review L3 (the reviewer's probe, kept as vectors).
  describe('a fill whose own candle is not held', () => {
    const yesterday = session([16], 60)
    it.each([
      ['a pre-open fill before the day’s first candle has loaded', ist(17, 9, 7)],
      ['a fill two seconds after the open, history ending yesterday', ist(17, 9, 15, 2)],
    ])('%s is never put on the previous day’s last candle', (_what, at) => {
      expect(barIndexOf(yesterday, sec(at), false)).toBe(NOT_HELD)
      const marks = tripMarkers([leg('entry', 'BUY', 65, 131.2, at)], yesterday, false, 2)
      expect(marks).toMatchObject({ markers: [], first: null, older: false, outside: 1 })
    })

    it('a fill on a day the minute history skips is not marked on the day before', () => {
      const holed = [...session([13], 60), ...session([17], 60)]
      expect(barIndexOf(holed, sec(ist(16, 11, 0)), false)).toBe(NOT_HELD)
    })

    it('is marked on its own candle once that is held, and the count goes', () => {
      const legs = [
        leg('entry', 'BUY', 65, 131.2, ist(16, 10, 2)),
        leg('exit', 'SELL', 65, 133, ist(17, 9, 15, 2)),
      ]
      expect(tripMarkers(legs, yesterday, false, 2)).toMatchObject({ outside: 1, older: false })
      const both = [...yesterday, sec(ist(17, 9, 15))]
      const marks = tripMarkers(legs, both, false, 2)
      expect(marks.outside).toBe(0)
      expect(marks.markers.map((m) => m.time)).toEqual([sec(ist(16, 10, 2)), sec(ist(17, 9, 15))])
    })

    it('says how many in words', () => {
      expect(outsideWords(1)).toBe('1 fill is outside the loaded candles.')
      expect(outsideWords(3)).toBe('3 fills are outside the loaded candles.')
    })
  })

  it('keeps the fills each mark stands for, for the sheet a tap opens', () => {
    const legs = [
      leg('entry', 'BUY', 65, 100, ist(16, 10, 0, 1)),
      leg('entry', 'BUY', 130, 101.5, ist(16, 10, 0, 40)),
      leg('exit', 'SELL', 195, 105, ist(16, 10, 20)),
    ]
    const marks = tripMarkers(legs, minutes, false, 2)
    expect(marks.fills.get(marks.markers[0].id)).toEqual([legs[0], legs[1]])
    expect(marks.fills.get(marks.markers[1].id)).toEqual([legs[2]])
  })
})

describe('the view (vectors)', () => {
  it.each([
    ['a short trade is widened to the least width', 100, 110, 400, { from: 85, to: 125 }],
    ['an entry alone', 100, 100, 400, { from: 80, to: 120 }],
    [
      'a two-hour trade on minutes keeps both ends, with a margin',
      200,
      320,
      400,
      { from: 188, to: 332 },
    ],
    ['near the last candle it stops a little after it', 380, 395, 400, { from: 362, to: 402 }],
    ['near the first candle it keeps a little room before it', 3, 10, 400, { from: -8, to: 32 }],
    ['wider than the most it opens on the entry', 50, 300, 400, { from: 42, to: 42 + 70 }],
    ['few candles: never padded wider than they are', 0, 0, 14, { from: -8, to: 16 }],
    ['a trade that ends at the last candle keeps both ends', 330, 395, 400, { from: 321, to: 402 }],
  ])('%s', (_what, first, last, count, want) => {
    expect(focusRange(first, last, count)).toEqual(want)
  })

  it('keeps every range it gives between the least and the most, and the entry in it', () => {
    for (const [first, last] of [
      [0, 0],
      [10, 15],
      [100, 290],
      [5, 395],
      [399, 399],
    ]) {
      const { from, to } = focusRange(first, last, 400)
      expect(to - from).toBeGreaterThanOrEqual(FOCUS_MIN_BARS)
      expect(to - from).toBeLessThanOrEqual(FOCUS_MAX_BARS)
      // The entry is in view with room for its label, and nothing past the room after the last.
      expect(from).toBeLessThanOrEqual(first - 8)
      expect(to).toBeGreaterThan(first)
      expect(to).toBeLessThanOrEqual(402)
      // Both ends in view, or a trade too wide for that opens on its entry.
      expect(to >= last || to - from === FOCUS_ENTRY_BARS).toBe(true)
    }
  })
})

// ---- the hook ---------------------------------------------------------------------------------

const MINUTES = session([16], 60)
const DAYS = [9, 10, 11, 12, 13, 16].map((d) => sec(ist(d, 0, 0)))

/** A trade of SBIN as the journal's trip_get answers it. */
function detail(over: Record<string, unknown> = {}) {
  return {
    id: 7,
    mode: 'live',
    exchange: 'NSE',
    symbol: 'SBIN',
    side: 'long',
    status: 'closed',
    opened_ms: ist(16, 10, 2, 5),
    closed_ms: ist(16, 10, 41, 50),
    legs: [
      { ...leg('entry', 'BUY', 10, 803.05, ist(16, 10, 2, 5)), trade_id: 'T1', order_id: 'O1' },
      { ...leg('exit', 'SELL', 10, 809.4, ist(16, 10, 41, 50)), trade_id: 'T2', order_id: 'O2' },
    ],
    ...over,
  }
}

class FakeChart implements TripChart {
  times: number[] = []
  listeners = new Set<(interval: string) => void>()
  barListeners = new Set<() => void>()
  onBars = (listener: () => void) => {
    this.barListeners.add(listener)
    return () => this.barListeners.delete(listener)
  }
  /** A new candle opened, or older ones were read in. */
  bars(times: number[]) {
    this.times = times
    act(() => {
      for (const listener of [...this.barListeners]) listener()
    })
  }
  setOverlays = vi.fn()
  showBars = vi.fn()
  barTimes = () => this.times
  onLoaded = (listener: (interval: string) => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  /** A load at `interval` drew `times`. */
  load(interval: string, times: number[]) {
    this.times = times
    act(() => {
      for (const listener of [...this.listeners]) listener(interval)
    })
  }
}

function mount(
  chart: FakeChart | null,
  search = '?trip=7',
  over: Partial<{ decimals: number }> = {}
) {
  const setTimeframe = vi.fn()
  const view = renderHook(
    (props: { owner: FakeChart | null; decimals: number }) =>
      useTripMarkers({
        owner: props.owner,
        tripId: tripIdOf(search),
        symbol: 'SBIN',
        exchange: 'NSE',
        decimals: props.decimals,
        setTimeframe,
      }),
    { initialProps: { owner: chart, decimals: over.decimals ?? 2 } }
  )
  return { view, setTimeframe }
}

async function settle() {
  await act(async () => {
    await Promise.resolve()
  })
}

const lastMarkers = (chart: FakeChart) =>
  chart.setOverlays.mock.lastCall?.[0]?.markers as Array<{ id: string; text: string; time: number }>

describe('the trade on the chart', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'], now: ist(16, 15, 0) })
    call.mockReset().mockResolvedValue({ enabled: true, data: detail() })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reads the trade once, chooses its interval, marks it and shows its entry', async () => {
    const chart = new FakeChart()
    const { setTimeframe } = mount(chart)
    await settle()
    expect(call).toHaveBeenCalledTimes(1)
    expect(call.mock.calls[0].slice(0, 2)).toEqual(['trip_get', { id: 7 }])
    expect(setTimeframe).toHaveBeenCalledWith('1m')
    expect(chart.setOverlays).not.toHaveBeenCalled()
    chart.load('1m', MINUTES)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05', 'S'])
    expect(chart.setOverlays.mock.lastCall?.[0]).not.toHaveProperty('lines')
    const entry = MINUTES.indexOf(sec(ist(16, 10, 2)))
    const exit = MINUTES.indexOf(sec(ist(16, 10, 41)))
    expect(chart.showBars).toHaveBeenCalledWith(focusRange(entry, exit, MINUTES.length))
    expect(call).toHaveBeenCalledTimes(1)
  })

  it('marks at once when the chart had already loaded the chosen interval', async () => {
    let answer: (value: unknown) => void = () => {}
    call.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    const chart = new FakeChart()
    mount(chart)
    chart.load('1m', MINUTES)
    expect(chart.setOverlays).not.toHaveBeenCalled()
    await act(async () => answer({ enabled: true, data: detail() }))
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05', 'S'])
    expect(chart.showBars).toHaveBeenCalledTimes(1)
  })

  it('lays the marks on every load, another timeframe the owner picks included', async () => {
    const chart = new FakeChart()
    mount(chart)
    await settle()
    chart.load('1m', MINUTES)
    chart.load('5m', session([16], 300))
    expect(lastMarkers(chart).map((m) => m.time)).toEqual([
      sec(ist(16, 10, 0)),
      sec(ist(16, 10, 40)),
    ])
    expect(chart.showBars).toHaveBeenCalledTimes(2)
  })

  it('a trade older than the candles at its interval says so and moves to daily candles', async () => {
    call.mockResolvedValue({
      enabled: true,
      data: detail({
        opened_ms: ist(12, 10, 2),
        closed_ms: ist(12, 10, 30),
        legs: [
          { ...leg('entry', 'BUY', 10, 790, ist(12, 10, 2)), trade_id: 'T1', order_id: 'O1' },
          { ...leg('exit', 'SELL', 10, 795, ist(12, 10, 30)), trade_id: 'T2', order_id: 'O2' },
        ],
      }),
    })
    const chart = new FakeChart()
    const { view, setTimeframe } = mount(chart)
    await settle()
    expect(setTimeframe).toHaveBeenLastCalledWith('1m')
    chart.load('1m', MINUTES)
    expect(view.result.current.notice).toBe(`${OLDER_THAN_HISTORY} ${SHOWN_DAILY}`)
    expect(setTimeframe).toHaveBeenLastCalledWith('D')
    expect(chart.showBars).not.toHaveBeenCalled()
    chart.load('D', DAYS)
    expect(lastMarkers(chart).map((m) => [m.text, m.time])).toEqual([
      ['B 790.00', sec(ist(12, 0, 0))],
      ['S', sec(ist(12, 0, 0))],
    ])
    expect(chart.showBars).toHaveBeenCalledTimes(1)
    // The sentence says why the chart is daily, and stays until dismissed.
    expect(view.result.current.notice).toBe(`${OLDER_THAN_HISTORY} ${SHOWN_DAILY}`)
    act(() => view.result.current.dismiss())
    expect(view.result.current.notice).toBeNull()
  })

  it('never overrides a timeframe the owner picks: it only says the trade is older', async () => {
    const chart = new FakeChart()
    const { view, setTimeframe } = mount(chart)
    await settle()
    chart.load('1m', MINUTES)
    setTimeframe.mockClear()
    chart.load('5m', session([16], 300).slice(20)) // picked; its history starts at 10:55
    expect(view.result.current.notice).toBe(`${OLDER_THAN_HISTORY} ${PICK_LONGER}`)
    expect(setTimeframe).not.toHaveBeenCalled()
    chart.load('15m', session([16], 900))
    expect(view.result.current.notice).toBeNull()
    expect(lastMarkers(chart).map((m) => m.time)).toEqual([
      sec(ist(16, 10, 0)),
      sec(ist(16, 10, 30)),
    ])
  })

  it('says the marks could not be shown when the journal is switched off', async () => {
    const words = 'The journal is switched off on the server.'
    call.mockResolvedValue({ enabled: false, data: null, message: words })
    const chart = new FakeChart()
    const { view, setTimeframe } = mount(chart)
    await settle()
    expect(view.result.current.notice).toBe(`${NOT_SHOWN} ${words}`)
    chart.load('5m', session([16], 300))
    expect(chart.setOverlays).not.toHaveBeenCalled()
    expect(setTimeframe).not.toHaveBeenCalled()
  })

  it('says the journal own words when the trade cannot be read', async () => {
    call.mockRejectedValue(
      new JournalError({
        code: 'refused',
        message: 'This trade is not in the journal any more. Open the list again.',
      })
    )
    const { view } = mount(new FakeChart())
    await settle()
    expect(view.result.current.notice).toBe(
      `${NOT_SHOWN} This trade is not in the journal any more. Open the list again.`
    )
  })

  it("marks nothing for a link to another instrument's trade", async () => {
    call.mockResolvedValue({ enabled: true, data: detail({ symbol: 'INFY' }) })
    const chart = new FakeChart()
    const { view, setTimeframe } = mount(chart)
    await settle()
    chart.load('5m', session([16], 300))
    expect(setTimeframe).not.toHaveBeenCalled()
    expect(chart.setOverlays).not.toHaveBeenCalled()
    expect(view.result.current.notice).toBeNull()
  })

  it('reads nothing and hears nothing without a trade in the address', async () => {
    const chart = new FakeChart()
    const { view, setTimeframe } = mount(chart, '')
    await settle()
    expect(call).not.toHaveBeenCalled()
    expect(chart.listeners.size).toBe(0)
    expect(setTimeframe).not.toHaveBeenCalled()
    expect(view.result.current.notice).toBeNull()
  })

  it('writes the prices again when the instrument tick arrives after the marks', async () => {
    const chart = new FakeChart()
    const { view } = mount(chart)
    await settle()
    chart.load('1m', MINUTES)
    view.rerender({ owner: chart, decimals: 3 })
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.050', 'S'])
    expect(chart.showBars).toHaveBeenCalledTimes(1)
  })

  it('stops hearing the chart when the screen goes', async () => {
    const chart = new FakeChart()
    const { view } = mount(chart)
    await settle()
    expect(chart.listeners.size).toBe(1)
    view.unmount()
    expect(chart.listeners.size).toBe(0)
  })
})

// ---- review v0.15.0: M1 (marks as fills come in), L3 (its own candle or none), L6 -------------

describe('the marks follow the trade while the chart is open (review M1)', () => {
  /** The trade still open: its entry only. */
  const open = () =>
    detail({
      status: 'open',
      closed_ms: null,
      legs: [
        { ...leg('entry', 'BUY', 10, 803.05, ist(16, 10, 2, 5)), trade_id: 'T1', order_id: 'O1' },
      ],
    })
  const visible = (state: 'visible' | 'hidden') => {
    Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
  }
  async function opened() {
    call.mockResolvedValue({ enabled: true, data: open() })
    const chart = new FakeChart()
    const mounted = mount(chart)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    chart.load('1m', MINUTES)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05'])
    call.mockResolvedValue({ enabled: true, data: detail() }) // the exit is in the journal now
    return { chart, ...mounted }
  }
  const wait = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms)
    })

  beforeEach(() => {
    vi.useFakeTimers({ now: ist(16, 10, 45) })
    call.mockReset()
    socket.connected = true
  })
  afterEach(() => {
    visible('visible')
    vi.useRealTimers()
  })

  it('an exit made while the chart is open gets its S after the order update, the view left alone', async () => {
    const { chart } = await opened()
    socket.emit('order_update', { symbol: 'SBIN', exchange: 'NSE', order_status: 'complete' })
    await wait(AFTER_ORDER_MS - 1)
    expect(call).toHaveBeenCalledTimes(1) // the journal needs a moment to record the fill
    await wait(1)
    expect(call).toHaveBeenCalledTimes(2)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05', 'S'])
    expect(chart.showBars).toHaveBeenCalledTimes(1)
  })

  it('a burst of updates is one read, and an analyzer-mode order counts too', async () => {
    const { chart } = await opened()
    for (let i = 0; i < 5; i++) {
      socket.emit('analyzer_update', { request: { symbol: 'SBIN', exchange: 'NSE' }, response: {} })
      await wait(500)
    }
    await wait(AFTER_ORDER_MS)
    expect(call).toHaveBeenCalledTimes(2)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05', 'S'])
  })

  it('an order of another instrument reads nothing, and nothing is read on a timer', async () => {
    await opened()
    socket.emit('order_update', { symbol: 'INFY', exchange: 'NSE' })
    socket.emit('analyzer_update', { request: { symbol: 'SBIN', exchange: 'BSE' } })
    await wait(30 * 60_000)
    expect(call).toHaveBeenCalledTimes(1)
    expect(frameIsFor({ symbol: 'SBIN', exchange: 'NSE' }, 'SBIN', 'NSE')).toBe(true)
    expect(frameIsFor({}, 'SBIN', 'NSE')).toBe(true)
  })

  it('reads again when the connection comes back, and on return to the app after a while', async () => {
    const { chart } = await opened()
    socket.emit('connect')
    await wait(0)
    expect(call).toHaveBeenCalledTimes(2)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05', 'S'])
    visible('hidden')
    socket.emit('order_update', { symbol: 'SBIN', exchange: 'NSE' })
    await wait(AFTER_ORDER_MS + AWAY_MS)
    expect(call).toHaveBeenCalledTimes(2) // a hidden page reads nothing
    visible('visible')
    await wait(0)
    expect(call).toHaveBeenCalledTimes(3)
  })

  it('a read that fails keeps the marks and says how old they are', async () => {
    const { chart, view } = await opened()
    call.mockRejectedValue(new JournalError({ code: 'network', message: 'No answer.' }))
    socket.emit('order_update', { symbol: 'SBIN', exchange: 'NSE' })
    await wait(AFTER_ORDER_MS)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05'])
    expect(view.result.current.notice).toBe(marksAsOf(ist(16, 10, 45)))
    expect(view.result.current.notice).toMatch(/^Marks as of 10:45: the journal did not answer\./)
    call.mockResolvedValue({ enabled: true, data: detail() })
    socket.emit('order_update', { symbol: 'SBIN', exchange: 'NSE' })
    await wait(AFTER_ORDER_MS)
    expect(view.result.current.notice).toBeNull()
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05', 'S'])
  })

  it('never moves the timeframe again after the first read', async () => {
    const { setTimeframe } = await opened()
    setTimeframe.mockClear()
    socket.emit('order_update', { symbol: 'SBIN', exchange: 'NSE' })
    await wait(AFTER_ORDER_MS)
    expect(setTimeframe).not.toHaveBeenCalled()
  })

  it('stops hearing the app when the screen goes', async () => {
    const { view } = await opened()
    view.unmount()
    expect([...socket.heard.values()].every((set) => set.size === 0)).toBe(true)
    socket.emit('order_update', { symbol: 'SBIN', exchange: 'NSE' })
    await wait(AFTER_ORDER_MS)
    expect(call).toHaveBeenCalledTimes(1)
  })
})

describe('a fill waits for its own candle (review L3)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'], now: ist(17, 9, 16) })
    call.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('is left unmarked with one sentence, then marked when its candle opens', async () => {
    const exit = ist(17, 9, 15, 2)
    call.mockResolvedValue({
      enabled: true,
      data: detail({
        closed_ms: exit,
        legs: [
          { ...leg('entry', 'BUY', 10, 803.05, ist(16, 15, 20)), trade_id: 'T1', order_id: 'O1' },
          { ...leg('exit', 'SELL', 10, 809.4, exit), trade_id: 'T2', order_id: 'O2' },
        ],
      }),
    })
    const chart = new FakeChart()
    const { view } = mount(chart)
    await settle()
    const fives = session([16], 300)
    chart.load('5m', fives)
    expect(lastMarkers(chart).map((m) => m.text)).toEqual(['B 803.05'])
    expect(view.result.current.notice).toBe('1 fill is outside the loaded candles.')
    chart.bars([...fives, sec(ist(17, 9, 15))])
    expect(lastMarkers(chart).map((m) => [m.text, m.time])).toEqual([
      ['B 803.05', sec(ist(16, 15, 20))],
      ['S', sec(ist(17, 9, 15))],
    ])
    expect(view.result.current.notice).toBeNull()
    expect(chart.showBars).toHaveBeenCalledTimes(1) // a new candle never moves the view
  })
})

describe('the address stops naming the trade (review L6)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'], now: ist(16, 15, 0) })
    call.mockReset().mockResolvedValue({ enabled: true, data: detail() })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function mountAt(chart: FakeChart) {
    const setTimeframe = vi.fn()
    const restoreTimeframe = vi.fn()
    const view = renderHook(
      ({ search }: { search: string }) =>
        useTripMarkers({
          owner: chart,
          tripId: tripIdOf(search),
          symbol: 'SBIN',
          exchange: 'NSE',
          decimals: 2,
          setTimeframe,
          restoreTimeframe,
        }),
      { initialProps: { search: '?trip=7' } }
    )
    return { view, restoreTimeframe }
  }

  it('clears the marks and puts the owner’s timeframe back', async () => {
    const chart = new FakeChart()
    const { view, restoreTimeframe } = mountAt(chart)
    await settle()
    chart.load('1m', MINUTES)
    expect(lastMarkers(chart)).toHaveLength(2)
    expect(view.result.current.fillsOf(lastMarkers(chart)[0].id)).toHaveLength(1)
    view.rerender({ search: '' })
    expect(chart.setOverlays).toHaveBeenLastCalledWith({ markers: [] })
    expect(restoreTimeframe).toHaveBeenCalledTimes(1)
    expect(view.result.current.notice).toBeNull()
    expect(chart.listeners.size).toBe(0)
  })

  it('leaves a timeframe the owner picked himself as it is', async () => {
    const chart = new FakeChart()
    const { view, restoreTimeframe } = mountAt(chart)
    await settle()
    chart.load('1m', MINUTES)
    chart.load('15m', session([16], 900))
    view.rerender({ search: '' })
    expect(chart.setOverlays).toHaveBeenLastCalledWith({ markers: [] })
    expect(restoreTimeframe).not.toHaveBeenCalled()
  })
})
