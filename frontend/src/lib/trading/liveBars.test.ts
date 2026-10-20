import { type Bar, SessionCalendar } from 'openalgo-charts'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  HistoryResync,
  LiveBarBuilder,
  LiveBarClock,
  type LiveBarUpdate,
  liveBucketing,
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
  it("folds today's ticks into today's bar, in history's own stamp", () => {
    // History stamps a day at midnight UTC, which is 05:30 in India.
    const today = Date.UTC(2026, 9, 20) / 1000
    const b = new LiveBarBuilder({ interval: 'D', session: nse() })
    b.seed({ time: today, open: 100, high: 105, low: 99, close: 104, volume: 80_000 })
    const u = b.onTick({ time: tue(14, 0), price: 106, dayVolume: 90_000 })
    expect(u?.isNew).toBe(false)
    expect(u?.bar).toMatchObject({ time: today, open: 100, high: 106, close: 106, volume: 80_000 })
    expect(b.onTick({ time: tue(14, 1), price: 105, dayVolume: 91_000 })?.bar.volume).toBe(81_000)
  })

  it("opens tomorrow's bar on its first tick, stamped the way history stamps", () => {
    const today = Date.UTC(2026, 9, 20) / 1000
    const b = new LiveBarBuilder({ interval: 'D', session: nse() })
    b.seed({ time: today, open: 100, high: 105, low: 99, close: 104, volume: 80_000 })
    const u = b.onTick({ time: ist(2026, 10, 22, 9, 15, 2), price: 103, dayVolume: 1_200 })
    expect(u?.isNew).toBe(true)
    expect(u?.bar).toMatchObject({ time: Date.UTC(2026, 9, 22) / 1000, open: 103, volume: 1_200 })
  })

  it("folds the week's days into one bar and carries the volume across them", () => {
    const monday = ist(2026, 10, 19, 0, 0)
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    b.seed({ time: monday, open: 100, high: 110, low: 95, close: 108, volume: 10_000 })
    // Thursday: the bar keeps what history said and grows from the first count.
    expect(
      b.onTick({ time: ist(2026, 10, 22, 9, 30), price: 109, dayVolume: 2_000 })?.bar
    ).toMatchObject({
      time: monday,
      volume: 10_000,
    })
    expect(
      b.onTick({ time: ist(2026, 10, 22, 15, 0), price: 111, dayVolume: 2_500 })?.bar
    ).toMatchObject({
      high: 111,
      volume: 10_500,
    })
    // Friday's count starts again from zero under the same weekly bar.
    expect(
      b.onTick({ time: ist(2026, 10, 23, 9, 30), price: 110, dayVolume: 100 })?.bar.volume
    ).toBe(10_500)
    expect(
      b.onTick({ time: ist(2026, 10, 23, 9, 31), price: 110, dayVolume: 600 })?.bar.volume
    ).toBe(11_000)
    // The next Monday opens the next week.
    const next = b.onTick({ time: ist(2026, 10, 26, 9, 15, 1), price: 112, dayVolume: 300 })
    expect(next?.isNew).toBe(true)
    expect(next?.bar).toMatchObject({ time: ist(2026, 10, 26, 0, 0), volume: 300 })
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

  it('ignores a running total that goes backwards inside a bar', () => {
    const b = new LiveBarBuilder({ interval: '1m', session: nse() })
    b.seed(bar(tue(10, 0), 100, 0))
    b.onTick({ time: tue(10, 0, 5), price: 100, dayVolume: 70_000 })
    b.onTick({ time: tue(10, 0, 6), price: 100, dayVolume: 70_300 })
    expect(b.onTick({ time: tue(10, 0, 7), price: 100, dayVolume: 70_200 })?.bar.volume).toBe(300)
    expect(b.onTick({ time: tue(10, 0, 8), price: 100, dayVolume: 70_250 })?.bar.volume).toBe(350)
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
    // History stamped the week by its first trading day, a Tuesday.
    const lastWeek = ist(2026, 10, 12, 0, 0)
    const b = new LiveBarBuilder({ interval: 'W', session: nse() })
    b.seed({ time: lastWeek, open: 90, high: 100, low: 89, close: 99, volume: 9_000 })
    const opened = b.onTick({ time: ist(2026, 10, 20, 10, 0), price: 101, dayVolume: 500 })
    expect(opened?.bar.time).toBe(ist(2026, 10, 19, 0, 0))
    const tuesday = ist(2026, 10, 20, 0, 0)
    const merged = b.mergeSnapshot([
      { time: lastWeek, open: 90, high: 100, low: 89, close: 99, volume: 9_000 },
      { time: ist(2026, 10, 19, 0, 0), open: 101, high: 101, low: 101, close: 101, volume: 500 },
      { time: tuesday, open: 100.5, high: 102, low: 100, close: 101.5, volume: 800 },
    ])
    expect(merged.map((x) => x.time)).toEqual([lastWeek, tuesday])
    expect(merged[1]).toMatchObject({ open: 100.5, close: 101, high: 102, volume: 800 })
    expect(b.onTick({ time: ist(2026, 10, 20, 10, 5), price: 103 })?.bar.time).toBe(tuesday)
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
