import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { type Bar, SessionCalendar } from 'openalgo-charts'
import { describe, expect, it } from 'vitest'
import { type ClosingWindow, LiveBarBuilder } from './liveBars'
import { calendarSpec, type TimingRow } from './sessionHours'

/**
 * SUDARSHAN review M1 and M2: from 7 Dec 2026 a stock with derivatives closes
 * by auction after the cash close (its print at a random moment 15:38-15:40,
 * taken until 15:45), and an index's closing value may come then too. Such a
 * print belongs to the day's last intraday candle on the cash grid, as the
 * alerts and the screener take it; it never opens a candle and is never
 * dropped. Before 7 Dec the print comes inside the session and the server
 * sends no window, so nothing changes. The windows are the server's
 * (regimes_vectors.json `closing`, sessions.closing_window).
 */
interface RegimeDay {
  date: string
  closing: [string, string] | null
}
const VECTORS = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'sudarshan', 'tests', 'regimes_vectors.json'), 'utf8')
) as { timings: TimingRow[]; days: RegimeDay[] }

/** UTC seconds of a wall-clock time in India on an ISO date. */
const at = (date: string, hhmmss: string) => {
  const [y, m, d] = date.split('-').map(Number)
  const [hh, mm, ss = 0] = hhmmss.split(':').map(Number)
  return Date.UTC(y, m - 1, d, hh, mm, ss) / 1000 - 19_800
}
const day = (date: string) => VECTORS.days.find((d) => d.date === date) as RegimeDay
const windowOf = (date: string): ClosingWindow | null => {
  const closing = day(date).closing
  return closing ? { from: at(date, closing[0]), until: at(date, closing[1]) } : null
}
const nse = (date: string) => new SessionCalendar(calendarSpec('NSE', VECTORS.timings, [], date))

/** The last candle of the cash session on each interval, and where history's bar starts. */
const LAST: Array<[string, string]> = [
  ['1m', '15:29'],
  ['5m', '15:25'],
  ['15m', '15:15'],
  ['1h', '15:15'],
]

function builder(date: string, interval: string, closing: ClosingWindow | null) {
  const b = new LiveBarBuilder({ interval, session: nse(date), closing })
  const start = LAST.find(([i]) => i === interval)?.[1] as string
  const seeded: Bar = {
    time: at(date, start),
    open: 100,
    high: 100.75,
    low: 99.5,
    close: 100.25,
    volume: 5000,
  }
  b.seed(seeded, at(date, '15:29:30'))
  // The last continuous trade, with the day's count.
  b.onTick({ time: at(date, '15:29:59'), price: 100.5, dayVolume: 1_000_000 })
  return { b, start: at(date, start) }
}

describe('the closing print after the cash close (review M1, M2)', () => {
  it('pins the windows on both sides of 7 Dec 2026', () => {
    expect(day('2026-12-04').closing).toBeNull()
    expect(day('2026-12-10').closing).toEqual(['15:30', '15:45'])
  })

  it.each(
    LAST
  )('Thu 10 Dec, %s: a stock with derivatives takes its auction print into the %s candle', (interval, last) => {
    const date = '2026-12-10'
    const { b, start } = builder(date, interval, windowOf(date))
    const u = b.onTick({ time: at(date, '15:39:40'), price: 103.25, dayVolume: 1_250_000 })
    expect(u?.bar.time, `the print goes to ${last}`).toBe(start)
    expect(u?.isNew).toBe(false)
    expect(u?.bar.close).toBe(103.25)
    expect(u?.bar.high).toBe(103.25)
    expect(b.current()?.time).toBe(start)
    // The auction's own volume joins the candle.
    expect(u?.bar.volume).toBeGreaterThanOrEqual(250_000)
    // The book after the print (the same count, the last continuous price) moves nothing.
    expect(b.onTick({ time: at(date, '15:40:00'), price: 100.5, dayVolume: 1_250_000 })).toBeNull()
    expect(b.current()?.close).toBe(103.25)
    // Nothing opens after the close on the clock either.
    expect(b.onClock(at(date, '15:41:00'))).toBeNull()
  })

  it("Thu 10 Dec: the auction's book before the print moves no bar and opens none", () => {
    const date = '2026-12-10'
    const { b, start } = builder(date, '1m', windowOf(date))
    // Upstox sends the auction's book once a minute, its trade time on the minute.
    for (const minute of ['15:31:00', '15:32:00', '15:33:00'])
      expect(b.onTick({ time: at(date, minute), price: 100.5, dayVolume: 1_000_000 })).toBeNull()
    expect(b.current()?.time).toBe(start)
    expect(b.current()?.close).toBe(100.5)
  })

  it('Thu 10 Dec: a print stamped just after 15:40 still counts; one past 15:45 does not', () => {
    const date = '2026-12-10'
    const late = builder(date, '5m', windowOf(date))
    expect(
      late.b.onTick({ time: at(date, '15:40:01'), price: 103.25, dayVolume: 1_250_000 })?.bar.close
    ).toBe(103.25)
    const after = builder(date, '5m', windowOf(date))
    expect(
      after.b.onTick({ time: at(date, '15:45:01'), price: 103.25, dayVolume: 1_250_000 })
    ).toBeNull()
  })

  it('Thu 10 Dec: the 15:29 minute nobody traded takes the print in its own place', () => {
    const date = '2026-12-10'
    const b = new LiveBarBuilder({ interval: '1m', session: nse(date), closing: windowOf(date) })
    b.seed(
      { time: at(date, '15:27'), open: 100, high: 100.5, low: 100, close: 100.5, volume: 900 },
      at(date, '15:27:30')
    )
    b.onTick({ time: at(date, '15:27:40'), price: 100.5, dayVolume: 1_000_000 })
    const u = b.onTick({ time: at(date, '15:39:40'), price: 103.25, dayVolume: 1_250_000 })
    expect(u?.bar.time).toBe(at(date, '15:29'))
    expect(u?.bar.close).toBe(103.25)
    expect(u?.bar.volume).toBe(250_000)
  })

  it('Thu 10 Dec: a stock without derivatives has no window; its trade after 15:30 moves no bar', () => {
    const date = '2026-12-10'
    // An hourly candle from 15:15 spans the close and keeps taking a later trade, as before 7 Dec.
    for (const [interval] of LAST.filter(([i]) => i !== '1h')) {
      const { b, start } = builder(date, interval, null)
      expect(
        b.onTick({ time: at(date, '15:39:40'), price: 103.25, dayVolume: 1_250_000 })
      ).toBeNull()
      expect(b.current()?.time).toBe(start)
      expect(b.current()?.close).toBe(100.5)
    }
  })

  it('Thu 10 Dec: an index has no count; a value in the window is its closing value', () => {
    const date = '2026-12-10'
    const index = new LiveBarBuilder({
      interval: '5m',
      session: nse(date),
      closing: windowOf(date),
    })
    index.seed(
      { time: at(date, '15:25'), open: 26000, high: 26010, low: 25990, close: 26005 },
      at(date, '15:29:30')
    )
    index.onTick({ time: at(date, '15:29:59'), price: 26004 })
    const u = index.onTick({ time: at(date, '15:39:41'), price: 26031.5 })
    expect(u?.bar.time).toBe(at(date, '15:25'))
    expect(u?.bar.close).toBe(26031.5)
    expect(u?.bar.high).toBe(26031.5)
    // Without a window (no such value is sent, or before 7 Dec) nothing changes.
    const plain = new LiveBarBuilder({ interval: '5m', session: nse(date) })
    plain.seed({ time: at(date, '15:25'), open: 26000, high: 26010, low: 25990, close: 26005 })
    expect(plain.onTick({ time: at(date, '15:39:41'), price: 26031.5 })).toBeNull()
  })

  it.each(
    LAST
  )('Fri 4 Dec, %s: no window, and the print inside the session is a trade as before', (interval) => {
    const date = '2026-12-04'
    expect(windowOf(date)).toBeNull()
    const b = new LiveBarBuilder({ interval, session: nse(date), closing: windowOf(date) })
    b.seed(
      { time: at(date, '15:15'), open: 100, high: 100.75, low: 99.5, close: 100.25, volume: 5000 },
      at(date, '15:20:00')
    )
    b.onTick({ time: at(date, '15:14:59'), price: 100.5, dayVolume: 1_000_000 })
    const u = b.onTick({ time: at(date, '15:28:09'), price: 103.25, dayVolume: 1_250_000 })
    const expected = interval === '1m' ? '15:28' : interval === '5m' ? '15:25' : '15:15'
    expect(u?.bar.time).toBe(at(date, expected))
    expect(u?.bar.close).toBe(103.25)
    // After the close nothing opens a bar, as before; an hourly candle from 15:15 spans the close.
    const after = b.onTick({ time: at(date, '15:31:00'), price: 104, dayVolume: 1_300_000 })
    if (interval === '1h') expect(after?.bar.time).toBe(at(date, '15:15'))
    else expect(after).toBeNull()
  })

  it('a day bar takes the print on both sides of 7 Dec, as it always did', () => {
    for (const date of ['2026-12-04', '2026-12-10']) {
      const b = new LiveBarBuilder({ interval: 'D', session: nse(date), closing: windowOf(date) })
      b.seed({ time: at(date, '00:00'), open: 99, high: 101, low: 98, close: 100.5, volume: 9e5 })
      const u = b.onTick({ time: at(date, '15:39:40'), price: 103.25, dayVolume: 1_250_000 })
      expect(u?.bar.close).toBe(103.25)
    }
  })
})
