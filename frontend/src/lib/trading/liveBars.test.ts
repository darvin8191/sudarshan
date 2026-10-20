import { type Bar, SessionCalendar } from 'openalgo-charts'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  HistoryResync,
  insertLiveBar,
  LiveBarBuilder,
  LiveBarClock,
  type LiveBarUpdate,
  liveBucketing,
  removeLiveBar,
  upsertLiveBar,
} from './liveBars'

/** UTC seconds of a wall-clock time in India. */
const ist = (y: number, mo: number, d: number, h: number, mi: number, s = 0) =>
  Date.UTC(y, mo - 1, d, h, mi, s) / 1000 - 19_800

/** A regular NSE week, with Wednesday 21 October 2026 a holiday. */
const nse = () =>
  new SessionCalendar({
    timezone: 'Asia/Kolkata',
    sessions: ['0915-1530:23456'],
    exceptions: { '2026-10-21': [] },
    preMarketMinutes: 15,
  })

const bar = (time: number, close: number, volume = 1000): Bar => ({
  time,
  open: close - 1,
  high: close + 1,
  low: close - 2,
  close,
  volume,
})

/** Tuesday 20 October 2026, at a minute of the session. */
const tue = (h: number, mi: number, s = 0) => ist(2026, 10, 20, h, mi, s)

afterEach(() => {
  vi.useRealTimers()
})

describe('liveBucketing', () => {
  it.each([
    ['1m', { kind: 'intraday', seconds: 60 }],
    ['3m', { kind: 'intraday', seconds: 180 }],
    ['1h', { kind: 'intraday', seconds: 3600 }],
    ['D', { kind: 'calendar', unit: 'day' }],
    ['W', { kind: 'calendar', unit: 'week' }],
    ['M', { kind: 'calendar', unit: 'month' }],
  ])('reads %s', (code, expected) => {
    expect(liveBucketing(code)).toEqual(expected)
  })

  it('has no live bar for a code it cannot bucket', () => {
    expect(liveBucketing('T500')).toBeNull()
    expect(LiveBarBuilder.supports('T500')).toBe(false)
  })
})

describe('a bar opens on the clock', () => {
  function streaming() {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 101, dayVolume: 50_000 })
    return b
  }

  it('opens the next bar at the boundary with no tick, flat at the last close', () => {
    const b = streaming()
    const u = b.onClock(tue(10, 1) + 0.5)
    expect(u).toEqual({
      bar: { time: tue(10, 1), open: 101, high: 101, low: 101, close: 101, volume: 0 },
      isNew: true,
      provisional: false,
      placeholder: true,
    })
    expect(b.isPlaceholder()).toBe(true)
  })

  it('makes the next tick its open, not an extreme measured from the old close', () => {
    const b = streaming()
    b.onClock(tue(10, 1))
    const u = b.onTick({ time: tue(10, 1, 4), price: 102.5, dayVolume: 50_400 })
    expect(u?.isNew).toBe(false)
    expect(u?.bar).toEqual({
      time: tue(10, 1),
      open: 102.5,
      high: 102.5,
      low: 102.5,
      close: 102.5,
      volume: 400,
    })
    expect(b.isPlaceholder()).toBe(false)
  })

  it('leaves the bar to a tick that got there first', () => {
    const b = streaming()
    b.onTick({ time: tue(10, 1, 0), price: 103, dayVolume: 50_100 })
    expect(b.onClock(tue(10, 1) + 0.5)).toBeNull()
    expect(b.current()?.close).toBe(103)
  })

  it('opens nothing when the clock slept through a bucket, whose price is not known', () => {
    const b = streaming()
    expect(b.onClock(tue(10, 3, 1))).toBeNull()
  })

  it("opens the session's first bar at the previous session's close", () => {
    const b = new LiveBarBuilder({ interval: '5m', session: nse() })
    b.seed(bar(ist(2026, 10, 19, 15, 25), 250))
    const u = b.onClock(tue(9, 15) + 0.5)
    expect(u?.bar).toMatchObject({ time: tue(9, 15), open: 250, close: 250, volume: 0 })
    // Nothing was streamed before it, so whatever opens it is provisional.
    expect(u?.provisional).toBe(true)
  })

  it('is driven by a timer aimed at each boundary', () => {
    vi.useFakeTimers()
    vi.setSystemTime(tue(10, 0, 30) * 1000)
    const b = streaming()
    const opened: LiveBarUpdate[] = []
    const clock = new LiveBarClock({ builder: () => b, onBar: (u) => opened.push(u) })
    clock.start()
    vi.advanceTimersByTime(29_000)
    expect(opened).toHaveLength(0)
    vi.advanceTimersByTime(2_000) // 10:01:01, past the boundary and its grace
    expect(opened.map((u) => u.bar.time)).toEqual([tue(10, 1)])
    vi.advanceTimersByTime(60_000)
    expect(opened.map((u) => u.bar.time)).toEqual([tue(10, 1), tue(10, 2)])
    clock.stop()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('asks history for the bar that closed, unless a tick already opened the next one there', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(tue(10, 0, 30) * 1000)
    const b = streaming()
    let held: Bar[] = [bar(tue(10, 0), 101)]
    const refresh = vi.fn(async () => held)
    const clock = new LiveBarClock({
      builder: () => b,
      onBar: () => {},
      history: () => ({ bars: () => held, refresh }),
    })
    clock.start()
    vi.advanceTimersByTime(31_000) // the clock opens 10:01
    vi.advanceTimersByTime(2_500)
    expect(refresh).toHaveBeenCalledOnce()
    // At 10:02 a tick reaches history first, which then repairs on its own.
    held = [...held, bar(tue(10, 2), 102)]
    vi.advanceTimersByTime(60_000)
    vi.advanceTimersByTime(2_500)
    expect(refresh).toHaveBeenCalledOnce()
    clock.stop()
  })
})

describe('no bar opens outside the session', () => {
  it('opens nothing at the close, at night, on a weekend, a holiday or in the pre-open', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(15, 29), 100))
    b.onTick({ time: tue(15, 29, 30), price: 100.5 })
    expect(b.onClock(tue(15, 30))).toBeNull()
    expect(b.onClock(tue(21, 0))).toBeNull()
    expect(b.onClock(ist(2026, 10, 24, 10, 0))).toBeNull() // Saturday
    expect(b.onClock(ist(2026, 10, 21, 10, 0))).toBeNull() // holiday
    expect(b.onClock(ist(2026, 10, 22, 9, 5))).toBeNull() // pre-open
    expect(b.current()?.time).toBe(tue(15, 29))
  })

  it('keeps a snapshot at night or a pre-open price from opening a bar', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(15, 29), 100))
    expect(b.onTick({ time: tue(21, 0), price: 100.5 })).toBeNull()
    expect(b.onTick({ time: ist(2026, 10, 22, 9, 7), price: 99 })).toBeNull()
    expect(b.current()).toEqual(bar(tue(15, 29), 100))
  })

  it('aims the clock at the next session after the close, and never without hours', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(15, 29), 100))
    expect(b.nextClockAt(tue(15, 29, 30))).toBe(ist(2026, 10, 22, 9, 15)) // past the holiday
    expect(b.nextClockAt(tue(10, 0, 30))).toBe(tue(10, 1))
    const bare = new LiveBarBuilder({ interval: '1m' })
    bare.seed(bar(tue(10, 0), 100))
    expect(bare.nextClockAt(tue(10, 0, 30))).toBeNull()
    expect(bare.onClock(tue(10, 1))).toBeNull()
  })

  it('runs no timer through a closed market beyond one wake-up', () => {
    vi.useFakeTimers()
    vi.setSystemTime(tue(15, 29, 30) * 1000)
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(15, 29), 100))
    const opened: LiveBarUpdate[] = []
    const clock = new LiveBarClock({ builder: () => b, onBar: (u) => opened.push(u) })
    clock.start()
    vi.advanceTimersByTime(60 * 60_000)
    expect(opened).toHaveLength(0)
    expect(vi.getTimerCount()).toBe(1)
    clock.stop()
  })

  it('keeps an interval that does not divide the day on the session open', () => {
    const b = new LiveBarBuilder({ interval: '75m', session: nse() })
    // History's last bar was the previous session's 14:15.
    b.seed(bar(ist(2026, 10, 19, 14, 15), 100))
    expect(b.bucketStart(tue(9, 16))).toBe(tue(9, 15))
    expect(b.bucketStart(tue(10, 31))).toBe(tue(10, 30))
  })
})

describe('day, week and month bars are live', () => {
  it("folds today's ticks into today's bar, in history's own stamp, its volume the day's count", () => {
    // History stamps a day at midnight UTC, which is 05:30 in India.
    const today = Date.UTC(2026, 9, 20) / 1000
    const b = new LiveBarBuilder({ interval: 'D', session: nse() })
    b.seed({ time: today, open: 100, high: 105, low: 99, close: 104, volume: 80_000 })
    const u = b.onTick({ time: tue(14, 0), price: 106, dayVolume: 90_000 })
    expect(u?.isNew).toBe(false)
    // The day's running total is the bar's volume: history's figure was a snapshot of it.
    expect(u?.bar).toMatchObject({ time: today, open: 100, high: 106, close: 106, volume: 90_000 })
    expect(b.onTick({ time: tue(14, 1), price: 105, dayVolume: 91_000 })?.bar.volume).toBe(91_000)
  })

  it("opens tomorrow's bar on its first tick, stamped the way history stamps", () => {
    const today = Date.UTC(2026, 9, 20) / 1000
    const b = new LiveBarBuilder({ interval: 'D', session: nse() })
    b.seed({ time: today, open: 100, high: 105, low: 99, close: 104, volume: 80_000 })
    const u = b.onTick({ time: ist(2026, 10, 22, 9, 15, 2), price: 103, dayVolume: 1_200 })
    expect(u?.isNew).toBe(true)
    expect(u?.bar).toMatchObject({ time: Date.UTC(2026, 9, 22) / 1000, open: 103, volume: 1_200 })
  })

  it("lays each day of the week on history's part, which ends with yesterday", () => {
    const monday = ist(2026, 10, 19, 0, 0)
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    // History's week, read on Thursday, runs to Wednesday.
    b.seed({ time: monday, open: 100, high: 110, low: 95, close: 108, volume: 10_000 })
    // Thursday's whole count goes on top of it.
    expect(b.onTick({ time: ist(2026, 10, 22, 9, 30), price: 109, dayVolume: 2_000 })?.bar).toEqual(
      { time: monday, open: 100, high: 110, low: 95, close: 109, volume: 12_000 }
    )
    expect(
      b.onTick({ time: ist(2026, 10, 22, 15, 0), price: 111, dayVolume: 2_500 })?.bar
    ).toMatchObject({ high: 111, close: 111, volume: 12_500 })
    // Friday starts its own count; Thursday's stays in the week.
    expect(
      b.onTick({ time: ist(2026, 10, 23, 9, 30), price: 110, dayVolume: 100 })?.bar
    ).toMatchObject({ high: 111, close: 110, volume: 12_600 })
    expect(
      b.onTick({ time: ist(2026, 10, 23, 9, 31), price: 110, dayVolume: 600 })?.bar.volume
    ).toBe(13_100)
    // The next Monday opens the next week, with that day's count.
    const next = b.onTick({ time: ist(2026, 10, 26, 9, 15, 1), price: 112, dayVolume: 300 })
    expect(next?.isNew).toBe(true)
    expect(next?.bar).toMatchObject({ time: ist(2026, 10, 26, 0, 0), volume: 300 })
  })

  it("puts today's true open and extremes on the week from a quote, not only what has ticked", () => {
    const monday = ist(2026, 10, 19, 0, 0)
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    b.seed({ time: monday, open: 100, high: 110, low: 95, close: 108, volume: 10_000 })
    b.onTick({ time: ist(2026, 10, 22, 14, 0), price: 109, dayVolume: 2_000 })
    // Thursday opened at 107 and traded down to 94 before the chart opened.
    const folded = b.foldDay({
      time: ist(2026, 10, 22, 14, 0, 1),
      open: 107,
      high: 109.5,
      low: 94,
      close: 109,
      volume: 2_050,
    })
    expect(folded?.bar).toEqual({
      time: monday,
      open: 100,
      high: 110,
      low: 94,
      close: 109,
      volume: 12_050,
    })
    // Another period's quote, or another timeframe, folds nothing.
    expect(b.foldDay({ ...bar(ist(2026, 10, 27, 10, 0), 1), volume: 1 })).toBeNull()
    const intraday = new LiveBarBuilder({ interval: '5m', session: nse() })
    intraday.seed(bar(tue(10, 0), 100))
    expect(intraday.foldDay(bar(tue(10, 0), 100))).toBeNull()
  })

  it('counts no day history already holds: a weekend snapshot, or a pre-open frame carrying the last trade', () => {
    const monday = ist(2026, 10, 19, 0, 0)
    const week = { time: monday, open: 100, high: 110, low: 95, close: 108, volume: 50_000 }
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    // Read on Saturday: history's week holds Monday to Friday.
    b.seed(week, ist(2026, 10, 24, 10, 0))
    const friday = ist(2026, 10, 23, 15, 29, 59)
    expect(b.onTick({ time: friday, tradeTime: friday, price: 108, dayVolume: 9_000 })).toBeNull()
    expect(
      b.foldDay({ time: friday, open: 104, high: 109, low: 103, close: 108, volume: 9_000 })
    ).toBeNull()
    expect(b.current()).toEqual(week)
  })

  it('gives a day back to history once a re-sync holds it', () => {
    const monday = ist(2026, 10, 19, 0, 0)
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    // Read on Tuesday morning: history's week is Monday.
    b.seed({ time: monday, open: 100, high: 104, low: 99, close: 103, volume: 10_000 }, tue(9, 0))
    expect(b.onTick({ time: tue(15, 29), price: 105, dayVolume: 12_000 })?.bar).toMatchObject({
      high: 105,
      close: 105,
      volume: 22_000,
    })
    // Thursday's re-sync (Wednesday is a holiday) holds Tuesday: nothing is counted twice.
    const resynced = { time: monday, open: 100, high: 105.5, low: 99, close: 105, volume: 22_400 }
    expect(b.mergeSnapshot([resynced], ist(2026, 10, 22, 8, 0))).toEqual([resynced])
    expect(
      b.onTick({ time: ist(2026, 10, 22, 9, 15, 5), price: 106, dayVolume: 1_000 })?.bar
    ).toMatchObject({ high: 106, close: 106, volume: 23_400 })
  })

  it("folds the month's ticks into one bar and opens the next month", () => {
    const first = ist(2026, 10, 1, 0, 0)
    const b = new LiveBarBuilder({ interval: 'M', session: nse() })
    b.seed({ time: first, open: 100, high: 120, low: 90, close: 115, volume: 500_000 })
    expect(b.onTick({ time: ist(2026, 10, 30, 11, 0), price: 121 })?.bar).toMatchObject({
      time: first,
      high: 121,
    })
    const next = b.onTick({ time: ist(2026, 11, 2, 9, 15, 1), price: 119 })
    expect(next?.isNew).toBe(true)
    expect(next?.bar.time).toBe(ist(2026, 11, 1, 0, 0))
  })

  it('opens no bar on the clock for a day, a week or a month', () => {
    const b = new LiveBarBuilder({ interval: 'D', session: nse() })
    b.seed({ time: Date.UTC(2026, 9, 19) / 1000, open: 1, high: 1, low: 1, close: 1 })
    expect(b.nextClockAt(tue(9, 0))).toBeNull()
    expect(b.onClock(tue(9, 15))).toBeNull()
  })
})

describe("volume from the day's running total", () => {
  it('keeps history volume on the first tick and grows by the change after it', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 1_000))
    expect(b.onTick({ time: tue(10, 0, 20), price: 100, dayVolume: 70_000 })?.bar.volume).toBe(
      1_000
    )
    expect(b.onTick({ time: tue(10, 0, 40), price: 100, dayVolume: 70_250 })?.bar.volume).toBe(
      1_250
    )
  })

  it('starts the next bar from the total where the last one ended', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 1_000))
    b.onTick({ time: tue(10, 0, 20), price: 100, dayVolume: 70_000 })
    const u = b.onTick({ time: tue(10, 1, 2), price: 100, dayVolume: 70_180 })
    expect(u?.isNew).toBe(true)
    expect(u?.bar.volume).toBe(180)
    expect(u?.provisional).toBe(false)
  })

  it("gives the session's first bar everything traded so far today", () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(ist(2026, 10, 19, 15, 29), 100, 900))
    b.onTick({ time: ist(2026, 10, 19, 15, 29, 50), price: 100, dayVolume: 4_000_000 })
    const u = b.onTick({ time: tue(9, 15, 1), price: 101, dayVolume: 12_000 })
    expect(u?.bar.volume).toBe(12_000)
  })

  it('counts nothing it cannot account for on a first tick that opens a bucket', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 1_000))
    const u = b.onTick({ time: tue(10, 2, 5), price: 100, dayVolume: 75_000 })
    expect(u?.bar.volume).toBe(0)
    expect(u?.provisional).toBe(true)
    expect(b.onTick({ time: tue(10, 2, 30), price: 100, dayVolume: 75_400 })?.bar.volume).toBe(400)
  })

  it('counts a gap in the stream into the bar it happened in, and not into the next', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 0))
    b.onTick({ time: tue(10, 0, 5), price: 100, dayVolume: 70_000 })
    // The stream drops at 10:00:10 and comes back.
    b.resync()
    // Same bar: the running total includes what traded during the gap.
    expect(b.onTick({ time: tue(10, 0, 50), price: 100, dayVolume: 70_900 })?.bar.volume).toBe(900)
    // The next bar after a gap counts from its own first tick, and history
    // fills in the rest, because where the total stood at its boundary was missed.
    b.resync()
    const next = b.onTick({ time: tue(10, 1, 30), price: 100, dayVolume: 71_500 })
    expect(next?.bar.volume).toBe(0)
    expect(next?.provisional).toBe(true)
    expect(b.onTick({ time: tue(10, 1, 40), price: 100, dayVolume: 71_560 })?.bar.volume).toBe(60)
  })

  it('carries on from a reconciled volume', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 0))
    b.onTick({ time: tue(10, 0, 5), price: 100, dayVolume: 70_000 })
    b.onTick({ time: tue(10, 0, 10), price: 100, dayVolume: 70_100 })
    expect(b.reconcile({ ...bar(tue(10, 0), 100), volume: 2_000 })?.volume).toBe(2_000)
    expect(b.onTick({ time: tue(10, 0, 20), price: 100, dayVolume: 70_150 })?.bar.volume).toBe(
      2_050
    )
  })

  it('sums last traded quantities when no total comes, and keeps history when neither does', () => {
    const b = new LiveBarBuilder({ interval: '1m' })
    b.seed(bar(tue(10, 0), 100, 1_000))
    expect(b.onTick({ time: tue(10, 0, 5), price: 100, ltq: 75 })?.bar.volume).toBe(1_075)
    expect(b.onTick({ time: tue(10, 0, 6), price: 100 })?.bar.volume).toBe(1_075)
    expect(b.hasTickVolume()).toBe(false)
  })

  it("keeps the day's highest count: a lower reading inside the day is an older one", () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 0))
    b.onTick({ time: tue(10, 0, 5), price: 100, dayVolume: 70_000 })
    b.onTick({ time: tue(10, 0, 6), price: 100, dayVolume: 70_300 })
    expect(b.onTick({ time: tue(10, 0, 7), price: 100, dayVolume: 70_200 })?.bar.volume).toBe(300)
    expect(b.onTick({ time: tue(10, 0, 8), price: 100, dayVolume: 70_250 })?.bar.volume).toBe(300)
    expect(b.onTick({ time: tue(10, 0, 9), price: 100, dayVolume: 70_400 })?.bar.volume).toBe(400)
  })

  it('never lets an older reading lower where the next bar starts counting', () => {
    const streamed = () => {
      const b = new LiveBarBuilder({ interval: '1m', session: nse() })
      b.seed(bar(tue(10, 0), 100, 0))
      b.onTick({ time: tue(10, 0, 5), price: 100, dayVolume: 70_000 })
      b.onTick({ time: tue(10, 0, 6), price: 100, dayVolume: 70_300 })
      // A reading from before the last one, as a lagging quote or frame sends it.
      b.onTick({ time: tue(10, 0, 7), price: 100, dayVolume: 70_200 })
      return b
    }
    // A trade opens 10:01: only what traded after 70,300 is its.
    const byTrade = streamed()
    expect(
      byTrade.onTick({ time: tue(10, 1, 1), price: 100.5, dayVolume: 70_350 })?.bar.volume
    ).toBe(50)
    // The clock opens it: the same.
    const byClock = streamed()
    byClock.onClock(tue(10, 1) + 0.1)
    expect(
      byClock.onTick({ time: tue(10, 1, 1), price: 100.5, dayVolume: 70_350 })?.bar.volume
    ).toBe(50)
  })
})

describe('ticks stamped with the last trade time', () => {
  // A feed that stamps each tick with the exchange's last-trade time sends a
  // book update after a boundary with the last trade before it. Such a tick
  // belongs to the bar before the forming one.
  function streaming() {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 101, dayVolume: 50_000 })
    return b
  }

  function drawn(b: LiveBarBuilder) {
    const bars: Bar[] = [b.current() as Bar]
    const draw = (u: LiveBarUpdate | null) => {
      if (!u) return u
      if (u.previous) expect(upsertLiveBar(bars, u.previous)).toBe('replace')
      upsertLiveBar(bars, u.bar)
      return u
    }
    return { bars, draw }
  }

  it("opens the clock's bar once, and makes the first trade inside it its open", () => {
    const b = streaming()
    const { bars, draw } = drawn(b)
    draw(b.onClock(tue(10, 1) + 0.1))
    // Book updates after the boundary still carry the 10:00:30 trade: nothing moves.
    expect(draw(b.onTick({ time: tue(10, 0, 30), price: 101, dayVolume: 50_000 }))).toBeNull()
    expect(b.isPlaceholder()).toBe(true)
    const first = draw(b.onTick({ time: tue(10, 1, 2), price: 102, dayVolume: 50_300 }))
    expect(first).toMatchObject({ isNew: false, placeholder: false })
    expect(first?.bar).toEqual({
      time: tue(10, 1),
      open: 102,
      high: 102,
      low: 102,
      close: 102,
      volume: 300,
    })
    // One bar for the bucket, at the bucket's own time, and a late clock adds none.
    expect(draw(b.onClock(tue(10, 1, 3)))).toBeNull()
    expect(bars.map((x) => x.time)).toEqual([tue(10, 0), tue(10, 1)])
  })

  it('gives a late trade from the previous interval to the previous bar, never the new one', () => {
    const b = streaming()
    const { bars, draw } = drawn(b)
    draw(b.onClock(tue(10, 1) + 0.1))
    // The last trade of 10:00, at 10:00:59, arrives after the clock opened 10:01.
    const late = draw(b.onTick({ time: tue(10, 0, 59), price: 101.6, dayVolume: 50_250 }))
    expect(late?.previous).toEqual({
      time: tue(10, 0),
      open: 99,
      high: 101.6,
      low: 98,
      close: 101.6,
      volume: 1_250,
    })
    // The new bar has not traded: it follows the last price and takes no volume.
    expect(late?.bar).toEqual({
      time: tue(10, 1),
      open: 101.6,
      high: 101.6,
      low: 101.6,
      close: 101.6,
      volume: 0,
    })
    expect(late).toMatchObject({ isNew: false, placeholder: true })
    // Its first trade is its open, and counts only what traded after the late one.
    const first = draw(b.onTick({ time: tue(10, 1, 1), price: 101.8, dayVolume: 50_400 }))
    expect(first?.bar).toMatchObject({ open: 101.8, close: 101.8, volume: 150 })
    expect(first?.previous).toBeUndefined()
    // A trade older than the previous bar's close widens nothing it already holds
    // and does not take the close back.
    expect(draw(b.onTick({ time: tue(10, 0, 58), price: 101.2, dayVolume: 50_240 }))).toBeNull()
    expect(bars).toEqual([
      { time: tue(10, 0), open: 99, high: 101.6, low: 98, close: 101.6, volume: 1_250 },
      { time: tue(10, 1), open: 101.8, high: 101.8, low: 101.8, close: 101.8, volume: 150 },
    ])
  })

  it('gives a late trade to the previous bar when a trade, not the clock, opened the new one', () => {
    const b = new LiveBarBuilder({ interval: '1m' })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 101, dayVolume: 50_000 })
    const opened = b.onTick({ time: tue(10, 1, 0), price: 102, dayVolume: 50_100 })
    expect(opened?.bar).toMatchObject({ time: tue(10, 1), open: 102, volume: 100 })
    // Delivered out of order: a trade at 10:00:59 after the one at 10:01:00.
    const late = b.onTick({ time: tue(10, 0, 59), price: 100.4, dayVolume: 50_080 })
    expect(late?.previous).toMatchObject({ time: tue(10, 0), close: 100.4, volume: 1_080 })
    expect(late?.bar).toEqual(opened?.bar)
  })

  it('forgets the previous bar once history holds it', () => {
    const b = streaming()
    b.onClock(tue(10, 1) + 0.1)
    b.mergeSnapshot([bar(tue(9, 59), 99), { ...bar(tue(10, 0), 101.6), volume: 1_300 }])
    // History is the previous bar's owner now; a late copy of its last trade changes nothing.
    expect(b.onTick({ time: tue(10, 0, 59), price: 101.6, dayVolume: 50_300 })).toMatchObject({
      bar: { time: tue(10, 1), close: 101.6 },
    })
    expect(b.onTick({ time: tue(10, 0, 59), price: 101.6, dayVolume: 50_300 })).toBeNull()
  })

  it("folds a pre-open book update into the last bar, opens nothing before the session, and starts the first bar flat at the auction's price", () => {
    const b = new LiveBarBuilder({ interval: '5m', session: nse() })
    b.seed(bar(ist(2026, 10, 19, 15, 25), 250, 9_000))
    // 09:03 on Tuesday: the feed still carries Monday's last trade, and today's count is nil.
    const folded = b.onTick({ time: ist(2026, 10, 19, 15, 29, 59), price: 250, dayVolume: 0 })
    expect(folded?.bar).toEqual(bar(ist(2026, 10, 19, 15, 25), 250, 9_000))
    // The opening auction trades at 09:07:30. No bar opens before the session.
    expect(b.onTick({ time: tue(9, 7, 30), price: 252, dayVolume: 40_000 })).toBeNull()
    expect(b.current()?.time).toBe(ist(2026, 10, 19, 15, 25))
    // 09:15: the session's first bar opens flat at the auction's price, and stays
    // provisional, so history's open (the auction) stands.
    const first = b.onClock(tue(9, 15) + 0.1)
    expect(first).toMatchObject({
      bar: { time: tue(9, 15), open: 252, close: 252, volume: 0 },
      placeholder: true,
      provisional: true,
    })
    // Book updates still carrying the auction trade change nothing.
    expect(b.onTick({ time: tue(9, 7, 30), price: 252, dayVolume: 40_000 })).toBeNull()
    // The session's first trade is the bar's open, and its volume includes the auction's.
    expect(b.onTick({ time: tue(9, 15, 1), price: 252.5, dayVolume: 41_000 })?.bar).toMatchObject({
      open: 252.5,
      volume: 41_000,
    })
  })

  it("leaves a day bar's open to history, whose open includes the auction", () => {
    const yesterday = Date.UTC(2026, 9, 19) / 1000
    const b = new LiveBarBuilder({ interval: 'D', session: nse() })
    b.seed({ time: yesterday, open: 100, high: 105, low: 99, close: 104, volume: 80_000 })
    b.onTick({ time: ist(2026, 10, 19, 15, 29), price: 104, dayVolume: 90_000 })
    const today = b.onTick({ time: tue(9, 15, 2), price: 103, dayVolume: 1_200 })
    expect(today).toMatchObject({ isNew: true, provisional: true })
  })
})

describe('history merges without duplicates', () => {
  it('follows history until a tick or the clock has moved the bar', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 1_000))
    const fresh = [bar(tue(9, 59), 99), bar(tue(10, 0), 103, 1_800)]
    expect(b.mergeSnapshot(fresh)).toEqual(fresh)
    expect(b.current()).toEqual(bar(tue(10, 0), 103, 1_800))
  })

  it('keeps one bar per bucket and the close from the ticks', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 1_000))
    b.onTick({ time: tue(10, 0, 30), price: 104 })
    const once = b.mergeSnapshot([bar(tue(9, 59), 99), { ...bar(tue(10, 0), 101), volume: 3_000 }])
    expect(once.map((x) => x.time)).toEqual([tue(9, 59), tue(10, 0)])
    expect(once[1]).toMatchObject({ close: 104, high: 104, volume: 3_000 })
    const twice = b.mergeSnapshot(once)
    expect(twice).toEqual(once)
  })

  it("puts a clock bar after history's last, once, and lets history's own bar replace it whole", () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 100 })
    b.onClock(tue(10, 1))
    const history = [bar(tue(9, 59), 99), bar(tue(10, 0), 100)]
    const merged = b.mergeSnapshot(history)
    expect(merged.map((x) => x.time)).toEqual([tue(9, 59), tue(10, 0), tue(10, 1)])
    expect(b.mergeSnapshot(merged).map((x) => x.time)).toEqual(merged.map((x) => x.time))
    // History has the bucket now: it traded at 101 to 102 before any tick
    // arrived here. The flat 100 the clock drew is not one of its prices.
    const traded = { time: tue(10, 1), open: 101, high: 102, low: 101, close: 101.5, volume: 600 }
    const repaired = b.mergeSnapshot([...history, traded])
    expect(repaired.at(-1)).toEqual(traded)
    expect(b.isPlaceholder()).toBe(false)
  })

  it("gives a provisional bar history's open", () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    const opened = b.onTick({ time: tue(10, 1, 10), price: 101 })
    expect(opened?.provisional).toBe(true)
    const merged = b.mergeSnapshot([
      bar(tue(10, 0), 100),
      { time: tue(10, 1), open: 100.5, high: 101.5, low: 100.2, close: 100.9, volume: 4_200 },
    ])
    expect(merged.at(-1)).toEqual({
      time: tue(10, 1),
      open: 100.5,
      high: 101.5,
      low: 100.2,
      close: 101,
      volume: 4_200,
    })
    expect(b.isProvisional()).toBe(false)
  })

  it('keeps a provisional bar provisional when its own copy comes back without history', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    const opened = b.onTick({ time: tue(10, 1, 10), price: 101 })
    // The history owner stored what it was handed and has not asked history yet.
    b.mergeSnapshot([bar(tue(10, 0), 100), opened!.bar])
    expect(b.isProvisional()).toBe(true)
    const merged = b.mergeSnapshot([
      bar(tue(10, 0), 100),
      { time: tue(10, 1), open: 100.6, high: 101, low: 100.4, close: 100.8, volume: 900 },
    ])
    expect(merged.at(-1)).toMatchObject({ open: 100.6, low: 100.4, close: 101, volume: 900 })
    expect(b.isProvisional()).toBe(false)
  })

  it("moves a week's bar to history's stamp rather than keeping two for one week", () => {
    // History stamped the week by its first trading day, a Tuesday, and runs
    // to yesterday; the chart opened on Thursday (Wednesday was a holiday).
    const lastWeek = ist(2026, 10, 12, 0, 0)
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    b.seed({ time: lastWeek, open: 90, high: 100, low: 89, close: 99, volume: 9_000 })
    const opened = b.onTick({ time: ist(2026, 10, 22, 10, 0), price: 101, dayVolume: 500 })
    expect(opened?.bar.time).toBe(ist(2026, 10, 19, 0, 0))
    const tuesday = ist(2026, 10, 20, 0, 0)
    const merged = b.mergeSnapshot([
      { time: lastWeek, open: 90, high: 100, low: 89, close: 99, volume: 9_000 },
      { time: ist(2026, 10, 19, 0, 0), open: 101, high: 101, low: 101, close: 101, volume: 500 },
      { time: tuesday, open: 100.5, high: 102, low: 100, close: 101.5, volume: 800 },
    ])
    expect(merged.map((x) => x.time)).toEqual([lastWeek, tuesday])
    // History's open and extremes for the days it has, today's on top.
    expect(merged[1]).toEqual({
      time: tuesday,
      open: 100.5,
      high: 102,
      low: 100,
      close: 101,
      volume: 1_300,
    })
    expect(b.onTick({ time: ist(2026, 10, 22, 10, 5), price: 103 })?.bar.time).toBe(tuesday)
  })

  it.todo(
    "a week that history stamps on Sunday: Monday-start weeks for now; check Upstox's weekly stamp live"
  )

  it('keeps the bar that just closed when a repair comes back without it', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 100.5 })
    b.onClock(tue(10, 1) + 0.1)
    // The broker's history is a minute behind.
    const merged = b.mergeSnapshot([bar(tue(9, 58), 98), bar(tue(9, 59), 99)])
    expect(merged.map((x) => x.time)).toEqual([tue(9, 58), tue(9, 59), tue(10, 0), tue(10, 1)])
    expect(merged[2]).toMatchObject({ close: 100.5 })
  })

  it('follows history when it is already a bucket ahead', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 100 })
    const merged = b.mergeSnapshot([bar(tue(10, 0), 100), bar(tue(10, 1), 105, 700)])
    expect(merged.map((x) => x.time)).toEqual([tue(10, 0), tue(10, 1)])
    expect(b.current()).toEqual(bar(tue(10, 1), 105, 700))
    const u = b.onTick({ time: tue(10, 1, 30), price: 106 })
    expect(u?.isNew).toBe(false)
    expect(u?.bar).toMatchObject({ time: tue(10, 1), open: 104, high: 106, close: 106 })
  })
})

/** A host's bar list, drawing every update the way the hosts do. */
function drawing(seed: Bar[]) {
  const bars = seed.slice()
  const draw = (u: LiveBarUpdate | null) => {
    if (!u) return u
    if (u.replaces !== undefined) removeLiveBar(bars, u.replaces)
    if (u.previous) insertLiveBar(bars, u.previous)
    upsertLiveBar(bars, u.bar)
    return u
  }
  return { bars, draw }
}

describe('a quiet market keeps one flat bar, at the tail', () => {
  it('moves the flat bar on at each boundary rather than leaving a trail of them, before and after a re-sync', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 100.5 })
    const { bars, draw } = drawing([bar(tue(9, 59), 99), b.current() as Bar])
    expect(draw(b.onClock(tue(10, 1) + 0.1))?.replaces).toBeUndefined()
    expect(draw(b.onClock(tue(10, 2) + 0.1))?.replaces).toBe(tue(10, 1))
    expect(draw(b.onClock(tue(10, 3) + 0.1))?.replaces).toBe(tue(10, 2))
    const minutes = (list: Bar[]) => list.map((x) => (x.time - tue(10, 0)) / 60)
    expect(minutes(bars)).toEqual([-1, 0, 3])
    expect(bars.at(-1)).toEqual({
      time: tue(10, 3),
      open: 100.5,
      high: 100.5,
      low: 100.5,
      close: 100.5,
      volume: 0,
    })
    const merged = b.mergeSnapshot([bar(tue(9, 59), 99), bar(tue(10, 0), 100.5)])
    expect(minutes(merged)).toEqual([-1, 0, 3])
  })

  it('leaves one flat bar after the last trade before a late close, not one a minute', () => {
    // The F&O calendar runs to 15:40; trading stops at 15:30.
    const nfo = new SessionCalendar({
      timezone: 'Asia/Kolkata',
      sessions: ['0915-1540:23456'],
      exceptions: {},
    })
    const b = new LiveBarBuilder({ interval: '1m', session: nfo })
    b.seed(bar(tue(15, 29), 100))
    b.onTick({ time: tue(15, 29, 59), price: 100.2 })
    const { bars, draw } = drawing([bar(tue(15, 28), 99), b.current() as Bar])
    for (let minute = 30; minute <= 40; minute++) draw(b.onClock(tue(15, minute) + 0.1))
    expect(bars.map((x) => x.time)).toEqual([tue(15, 28), tue(15, 29), tue(15, 39)])
    expect(bars.at(-1)).toMatchObject({ open: 100.2, close: 100.2, volume: 0 })
  })

  it('asks history to repair a bar somebody traded, and not one nobody did', () => {
    vi.useFakeTimers()
    vi.setSystemTime(tue(10, 0, 30) * 1000)
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 100.5 })
    const refresh = vi.fn(async () => [])
    const clock = new LiveBarClock({
      builder: () => b,
      onBar: () => {},
      history: () => ({ bars: () => [bar(tue(10, 0), 100)], refresh }),
    })
    clock.start()
    vi.advanceTimersByTime(3.5 * 60_000) // 10:01 closes 10:00; 10:02 and 10:03 move the flat bar
    expect(refresh).toHaveBeenCalledOnce()
    clock.stop()
  })

  it('puts a bar in time order, and takes one out by its time', () => {
    const list = [bar(60, 1), bar(180, 3)]
    expect(insertLiveBar(list, bar(120, 2))).toBe('insert')
    expect(insertLiveBar(list, bar(180, 4))).toBe('replace')
    expect(insertLiveBar(list, bar(240, 5))).toBe('append')
    expect(list.map((x) => [x.time, x.close])).toEqual([
      [60, 1],
      [120, 2],
      [180, 4],
      [240, 5],
    ])
    expect(removeLiveBar(list, 180)).toBe(true)
    expect(removeLiveBar(list, 300)).toBe(false)
    expect(list.map((x) => x.time)).toEqual([60, 120, 240])
  })
})

describe('what the stream may be late with', () => {
  it("gives the session's first bar the whole day's count, when the clock opened it", () => {
    const monday = (h: number, mi: number, s = 0) => ist(2026, 10, 19, h, mi, s)
    const b = new LiveBarBuilder({ interval: '5m', session: nse() })
    b.seed(bar(monday(15, 25), 250, 9_000))
    // Yesterday's last trade, with yesterday's total, then the night.
    b.onTick({ time: monday(15, 29, 59), price: 250, dayVolume: 4_000_000 })
    expect(b.onClock(tue(9, 15) + 0.1)?.placeholder).toBe(true)
    expect(b.onTick({ time: tue(9, 15, 1), price: 252.5, dayVolume: 41_000 })?.bar).toMatchObject({
      open: 252.5,
      volume: 41_000,
    })
    expect(b.onTick({ time: tue(9, 15, 2), price: 252.6, dayVolume: 41_500 })?.bar.volume).toBe(
      41_500
    )
  })

  it("counts a later first bar from today's count, never from yesterday's or the whole day's", () => {
    const monday = (h: number, mi: number, s = 0) => ist(2026, 10, 19, h, mi, s)
    const open = (auction: boolean) => {
      const b = new LiveBarBuilder({ interval: '1m', session: nse() })
      b.seed(bar(monday(15, 29), 250, 9_000))
      b.onTick({ time: monday(15, 29, 59), price: 250, dayVolume: 4_000_000 })
      if (auction) b.onTick({ time: tue(9, 7, 30), price: 252, dayVolume: 40_000 })
      b.onClock(tue(9, 15) + 0.1)
      // Nobody trades 09:15 once the session opens; the flat bar moves on.
      expect(b.onClock(tue(9, 16) + 0.1)?.replaces).toBe(tue(9, 15))
      return b
    }
    // The auction was streamed: 09:16 counts what traded after it.
    const seen = open(true)
    expect(seen.onTick({ time: tue(9, 16, 5), price: 252.5, dayVolume: 41_000 })?.bar.volume).toBe(
      1_000
    )
    // It was not: 09:16 counts from its own first trade, and history fills in the rest.
    const missed = open(false)
    expect(
      missed.onTick({ time: tue(9, 16, 5), price: 252.5, dayVolume: 41_000 })?.bar.volume
    ).toBe(0)
    expect(
      missed.onTick({ time: tue(9, 16, 9), price: 252.5, dayVolume: 41_400 })?.bar.volume
    ).toBe(400)
  })

  it('gives the only trades of an interval the flat bar moved on from a bar of their own, all at their price', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    b.onTick({ time: tue(10, 0, 30), price: 100, dayVolume: 50_000 })
    const { bars, draw } = drawing([b.current() as Bar])
    draw(b.onClock(tue(10, 1) + 0.1)) // nobody trades 10:01 as it forms
    draw(b.onClock(tue(10, 2) + 0.1)) // the flat bar moves on to 10:02
    // The only 10:01 trade, at 10:01:59, delivered after the boundary.
    const late = draw(b.onTick({ time: tue(10, 1, 59), price: 102, dayVolume: 50_300 }))
    expect(late?.previous).toEqual({
      time: tue(10, 1),
      open: 102,
      high: 102,
      low: 102,
      close: 102,
      volume: 300,
    })
    expect(late?.bar).toMatchObject({ time: tue(10, 2), open: 102, close: 102, volume: 0 })
    expect(late?.placeholder).toBe(true)
    expect(bars.map((x) => x.time)).toEqual([tue(10, 0), tue(10, 1), tue(10, 2)])
    // 10:02's first trade counts only what traded after the late one.
    expect(draw(b.onTick({ time: tue(10, 2, 3), price: 102.4, dayVolume: 50_450 }))?.bar).toEqual({
      time: tue(10, 2),
      open: 102.4,
      high: 102.4,
      low: 102.4,
      close: 102.4,
      volume: 150,
    })
  })

  it('keeps the bar before the forming one through a repair, so a late trade still reaches it', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 0))
    b.onTick({ time: tue(10, 0, 30), price: 100, dayVolume: 10_000 })
    const { bars, draw } = drawing([bar(tue(9, 59), 99), b.current() as Bar])
    // A clock running fast opens 10:01 at 10:00:40 by the exchange's clock.
    draw(b.onClock(tue(10, 1) + 0.1))
    draw(b.onTick({ time: tue(10, 0, 41), price: 100.4, dayVolume: 10_100 }))
    // The repair after the boundary brings a copy of 10:00 that is already old.
    const partial = {
      time: tue(10, 0),
      open: 99.5,
      high: 100.4,
      low: 99,
      close: 100.4,
      volume: 1_100,
    }
    bars.splice(0, bars.length, ...b.mergeSnapshot([bar(tue(9, 59), 99), partial]))
    // A spike at 10:00:50 belongs to 10:00.
    const spike = draw(b.onTick({ time: tue(10, 0, 50), price: 103, dayVolume: 10_300 }))
    expect(spike?.previous).toMatchObject({ time: tue(10, 0), open: 99.5, high: 103, close: 103 })
    expect(bars.find((x) => x.time === tue(10, 0))).toMatchObject({ high: 103, close: 103 })
    expect(bars.at(-1)).toMatchObject({ time: tue(10, 1), open: 103, close: 103, volume: 0 })
  })

  it("ignores a count read before the stream's last one, from a quote or from the stream", () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 0))
    b.onTick({ time: tue(10, 0, 5), price: 100, dayVolume: 1_000_000 })
    expect(b.onTick({ time: tue(10, 0, 10), price: 100, dayVolume: 1_000_500 })?.bar.volume).toBe(
      500
    )
    // A quote answered a second earlier, delivered after the stream resumed.
    expect(
      b.onTick({ time: tue(10, 0, 11), price: 100, dayVolume: 999_800, polled: true })
    ).toBeNull()
    expect(b.onTick({ time: tue(10, 0, 11), price: 100, dayVolume: 999_800 })?.bar.volume).toBe(500)
    expect(b.onTick({ time: tue(10, 0, 12), price: 100, dayVolume: 1_000_600 })?.bar.volume).toBe(
      600
    )
  })

  it("never lets a quote set where a bar's count starts, or count a repeat as a trade", () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 1_000))
    // The stream is down; a quote answers with the day's count.
    const quote = { time: tue(10, 0, 20), price: 100.5, dayVolume: 70_000, polled: true }
    expect(b.onTick(quote)?.bar).toMatchObject({ close: 100.5, volume: 1_000 })
    expect(b.onTick({ ...quote, time: tue(10, 0, 24) })).toBeNull()
    // The stream is back: its first count is where the bar's count grows from.
    expect(b.onTick({ time: tue(10, 0, 30), price: 100.6, dayVolume: 70_200 })?.bar.volume).toBe(
      1_000
    )
    expect(b.onTick({ time: tue(10, 0, 40), price: 100.6, dayVolume: 70_300 })?.bar.volume).toBe(
      1_100
    )
    // A quote's first count of a new day starts nothing either: the next
    // day's count grows from the stream's first reading of it.
    const thu = (s: number) => ist(2026, 10, 22, 9, 16, s)
    expect(b.onTick({ time: thu(0), price: 101, dayVolume: 5_000, polled: true })?.bar.volume).toBe(
      0
    )
    expect(b.onTick({ time: thu(30), price: 101, dayVolume: 5_200 })?.bar.volume).toBe(0)
    expect(b.onTick({ time: thu(40), price: 101, dayVolume: 5_300 })?.bar.volume).toBe(100)
  })
})

describe('trades say the market is open', () => {
  it('opens bars for trades that keep flowing while the calendar says shut, but not in the pre-open and not on the clock', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(15, 29), 100))
    const trade = (time: number, price: number) => b.onTick({ time, tradeTime: time, price })
    // 18:00 on a special session the calendar does not list. One trade is not a flow.
    expect(trade(tue(18, 0, 0), 101)).toBeNull()
    expect(trade(tue(18, 0, 20), 101.5)).toMatchObject({
      isNew: true,
      bar: { time: tue(18, 0), open: 101.5, close: 101.5 },
    })
    expect(trade(tue(18, 0, 40), 102)?.bar).toMatchObject({ time: tue(18, 0), high: 102 })
    expect(trade(tue(18, 1, 5), 101.8)).toMatchObject({ isNew: true, bar: { time: tue(18, 1) } })
    // The clock still keeps to the calendar.
    expect(b.onClock(tue(18, 2) + 0.1)).toBeNull()
    // A frame whose own stamp moves on, with no trade time, is not a trade.
    expect(b.onTick({ time: tue(18, 5, 0), price: 101.8 })).toBeNull()
    // The pre-open's book moves on its trade times too, and opens nothing.
    const pre = (s: number) => ist(2026, 10, 22, 9, 7, s)
    expect(trade(pre(30), 103)).toBeNull()
    expect(trade(pre(40), 103.5)).toBeNull()
    expect(b.current()?.time).toBe(tue(18, 1))
  })
})

describe("the countdown to the forming bar's close", () => {
  it("runs to the bar's close in the session, says when the session closes first, and is off otherwise", () => {
    const hourly = new LiveBarBuilder({ interval: '1h', session: nse() })
    hourly.seed(bar(tue(14, 15), 100))
    expect(hourly.countdown(tue(14, 20))).toEqual({ kind: 'bar' })
    // 15:15 to 16:15 would run past the 15:30 close.
    expect(hourly.countdown(tue(15, 20))).toEqual({ kind: 'closes', at: tue(15, 30) })
    expect(hourly.countdown(tue(16, 0))).toEqual({ kind: 'off' })
    const day = new LiveBarBuilder({ interval: 'D', session: nse() })
    day.seed({ time: Date.UTC(2026, 9, 20) / 1000, open: 1, high: 1, low: 1, close: 1 })
    expect(day.countdown(tue(10, 0))).toEqual({ kind: 'off' })
    const bare = new LiveBarBuilder({ interval: '5m' })
    bare.seed(bar(tue(10, 0), 100))
    expect(bare.countdown(tue(10, 1))).toEqual({ kind: 'off' })
  })
})

describe('upsertLiveBar', () => {
  it('replaces by time, appends a newer bar and never inserts out of order', () => {
    const list = [bar(60, 1), bar(120, 2)]
    expect(upsertLiveBar(list, bar(120, 3))).toBe('replace')
    expect(upsertLiveBar(list, bar(180, 4))).toBe('append')
    expect(upsertLiveBar(list, bar(90, 5))).toBe('skip')
    expect(list.map((x) => [x.time, x.close])).toEqual([
      [60, 1],
      [120, 3],
      [180, 4],
    ])
  })
})

describe('HistoryResync', () => {
  it('re-reads every 30 seconds while ticks carry no volume, then every two minutes', () => {
    vi.useFakeTimers()
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100))
    const refresh = vi.fn()
    const resync = new HistoryResync({ builder: () => b, refresh })
    resync.start()
    vi.advanceTimersByTime(30_000)
    expect(refresh).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(30_000)
    expect(refresh).toHaveBeenCalledTimes(2)
    b.onTick({ time: tue(10, 0, 30), price: 100, dayVolume: 1_000 })
    vi.advanceTimersByTime(30_000) // the wait already set runs out
    expect(refresh).toHaveBeenCalledTimes(3)
    vi.advanceTimersByTime(119_000)
    expect(refresh).toHaveBeenCalledTimes(3)
    vi.advanceTimersByTime(1_000)
    expect(refresh).toHaveBeenCalledTimes(4)
    resync.stop()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('skips a re-read nobody is looking at', () => {
    vi.useFakeTimers()
    const refresh = vi.fn()
    let visible = false
    const resync = new HistoryResync({ builder: () => null, refresh, active: () => visible })
    resync.start()
    vi.advanceTimersByTime(30_000)
    expect(refresh).not.toHaveBeenCalled()
    visible = true
    vi.advanceTimersByTime(30_000)
    expect(refresh).toHaveBeenCalledOnce()
    resync.stop()
  })
})
