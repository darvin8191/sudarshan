/**
 * Candles on time and the phone waking (M4.1, C13), played from the committed
 * tick tapes against the real chart engine and the real live-bar pipeline.
 *
 * Each test here pins a promise the owner's broker apps keep, so a change that
 * breaks one turns it red: a candle nobody traded still opens on time, by the
 * exchange's clock and not the phone's; a trade that arrives late goes to the
 * candle it was traded in; back from a sleep the chart says Reconnecting at
 * once, reads the whole gap from its last candle to now, replaces a socket
 * that died, and holds one candle a bucket.
 */
import { type Bar, OpenAlgoDataFeed, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { intervalSeconds } from '@/lib/trading/intervals'
import { PROBE_TIMEOUT_MS } from '@/lib/trading/liveTicks'
import boundaryNoTrade from '@/test/tapes/boundary-no-trade-5m.json'
import lateTrade from '@/test/tapes/late-trade-5m.json'
import { ist } from '@/test/tapes/scenarios'
import wakeGap from '@/test/tapes/wake-gap-1m.json'
import wakeSilent from '@/test/tapes/wake-silent-5m.json'
import { depthFrame, FakeProxySocket, socketSink, type Tape, TapePlayer } from '@/test/tickTape'
import { apiInterval } from './phoneChart'
import {
  barsOf,
  historyOf,
  host,
  installHost,
  lineOf,
  mountChart,
  removeHost,
  setVisibility,
} from './testing/host'

vi.mock('@/lib/trading/sessionHours', () => ({
  applySessionHours: () => {},
  sessionCalendarFor: async () =>
    new SessionCalendar({
      timezone: 'Asia/Kolkata',
      sessions: ['0915-1530:23456'],
      exceptions: { '2026-10-21': [] },
    }),
}))
vi.mock('@/lib/trading/previousClose', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/previousClose')>()),
  previousClose: async () => null,
}))

const sec = (ms: number) => Math.floor(ms / 1000)
/** UTC seconds of midnight in India on the day of an instant in UTC seconds. */
const istMidnight = (t: number) => Math.floor((t + 19_800) / 86_400) * 86_400 - 19_800

/**
 * What the exchange's history holds on an interval, up to `untilMs`: today's
 * candles from 09:15 to the one forming then for an intraday interval, and
 * every trading day of October up to that day for a day bar (Wednesday 21 is a
 * holiday here). The last candle closes at `close`, the ones before a step
 * lower each. A quiet instrument's history ends at its last trade; a liquid
 * one's runs to the moment it is read.
 */
function exchangeBars(interval: string, untilMs: number, close = 100): Bar[] {
  const until = sec(untilMs)
  const times: number[] = []
  if (interval === 'D') {
    for (let d = 1; d <= 31; d++) {
      const time = Date.UTC(2026, 9, d) / 1000 - 19_800
      const weekday = new Date(Date.UTC(2026, 9, d)).getUTCDay()
      if (time > until) break
      if (weekday !== 0 && weekday !== 6 && d !== 21) times.push(time)
    }
  } else {
    const step = intervalSeconds(interval) as number
    const open = istMidnight(until) + 9 * 3600 + 15 * 60
    for (let t = open; t <= until && t < open + 375 * 60; t += step) times.push(t)
  }
  return times.map((time, i) => {
    const c = Math.round((close - (times.length - 1 - i) * 0.05) * 100) / 100
    return { time, open: c - 0.25, high: c + 0.5, low: c - 0.5, close: c, volume: 1_000 }
  })
}

/** History's answer: a liquid instrument's to the moment it is read, a quiet one's to `until`. */
function answerHistory(chip: string, { until = Infinity, close = 100 } = {}): void {
  host.replies.history = () =>
    historyOf(exchangeBars(apiInterval(chip), Math.min(Date.now(), until), close))
}

/** One candle per bucket, in time order. */
function expectOneBarPerBucket(bars: readonly Bar[]): void {
  const times = bars.map((bar) => bar.time)
  expect(new Set(times).size).toBe(times.length)
  expect(times).toEqual([...times].sort((a, b) => a - b))
}

/** Plays a tape into the chart's socket, with the phone's sleep as Android has it. */
function player(tape: Tape, { asleep = true } = {}): TapePlayer {
  return new TapePlayer(
    tape,
    {
      frame: socketSink(),
      page: setVisibility,
      sleep: asleep
        ? (ms) => {
            // The wall clock jumps; every timer keeps its remaining wait.
            vi.setSystemTime(Date.now() + ms)
          }
        : undefined,
    },
    (ms) => vi.advanceTimersByTimeAsync(ms)
  )
}

/** Opens a chart on `chip` at the tape's start, its socket accepted. */
async function openAt(
  tape: Tape,
  symbol: string,
  exchange: string,
  chip: string,
  history: { until?: number; close?: number } = {}
) {
  vi.setSystemTime(tape.start)
  answerHistory(chip, history)
  const view = mountChart()
  await view.chart.load(symbol, exchange, chip)
  await vi.advanceTimersByTimeAsync(0)
  const socket = FakeProxySocket.latest() as FakeProxySocket
  socket.accept()
  return { ...view, socket }
}

beforeEach(() => installHost(ist(10, 0, 30)))
afterEach(() => removeHost())

describe('candles open on time', () => {
  it('opens the 10:05 candle at 10:05 with nobody trading, flat, and the first trade replaces it', async () => {
    const tape = boundaryNoTrade as Tape
    // A quiet name: history holds nothing past the scenario's start.
    const { chart } = await openAt(tape, 'SBIN', 'NSE', '5m', { until: tape.start, close: 110.5 })
    const play = player(tape)
    await play.to(ist(10, 4, 59.9) - tape.start)
    expect(barsOf(chart).at(-1)?.time).toBe(sec(ist(10, 0)))
    // The clock's own grace past the boundary, and no more.
    await play.to(ist(10, 5, 0.25) - tape.start)
    expect(barsOf(chart).at(-1)).toEqual({
      time: sec(ist(10, 5)),
      open: 110.6,
      high: 110.6,
      low: 110.6,
      close: 110.6,
      volume: 0,
    })
    await play.to(ist(10, 5, 20) - tape.start)
    expect(barsOf(chart).at(-1)).toMatchObject({ time: sec(ist(10, 5)), open: 110.4, close: 110.4 })
    await play.end()
    expect(barsOf(chart).at(-1)).toMatchObject({
      time: sec(ist(10, 5)),
      open: 110.4,
      high: 110.7,
      close: 110.7,
      volume: 450,
    })
    expectOneBarPerBucket(barsOf(chart))
  })

  it('gives a trade that reaches the phone after the boundary to the candle it was traded in', async () => {
    const tape = lateTrade as Tape
    const { chart, heard } = await openAt(tape, 'SBIN', 'NSE', '5m', {
      until: tape.start,
      close: 110.5,
    })
    const play = player(tape)
    await play.to(ist(10, 5, 0.6) - tape.start)
    const [closed, forming] = barsOf(chart).slice(-2)
    expect(closed).toMatchObject({ time: sec(ist(10, 0)), close: 110.8 })
    // The book moved with no trade: the forming candle is still flat at the late trade.
    expect(forming).toMatchObject({ time: sec(ist(10, 5)), open: 110.8, close: 110.8, volume: 0 })
    await play.end()
    expect(barsOf(chart).at(-1)).toMatchObject({
      time: sec(ist(10, 5)),
      open: 111,
      close: 111,
      volume: 150,
    })
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 111, tradeTime: sec(ist(10, 5, 1)) })
    expectOneBarPerBucket(barsOf(chart))
  })

  /** The next boundary after an instant, for buckets counted from the 09:15 open. */
  const boundaryAfter = (t: number, step: number) => {
    const open = istMidnight(t) + 9 * 3600 + 15 * 60
    return open + Math.ceil((t - open + 1) / step) * step
  }

  it.each([
    ['1m', 0],
    ['3m', 0],
    ['5m', 20_000],
    ['15m', -20_000],
    ['1h', 0],
  ] as const)('opens a %s candle on its boundary by the exchange clock, the phone %i ms off it', async (chip, phoneAheadMs) => {
    const step = intervalSeconds(chip) as number
    const exchangeNow = ist(10, 20, 0)
    const boundary = boundaryAfter(sec(exchangeNow), step)
    // A quiet instrument: one trade half a minute before the boundary, then nothing.
    const start = boundary * 1000 - 30_000
    vi.setSystemTime(start + phoneAheadMs)
    answerHistory(chip, { until: start, close: 120.5 })
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', chip)
    await vi.advanceTimersByTimeAsync(0)
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    socket.pong(-phoneAheadMs)
    socket.receive({
      type: 'market_data',
      symbol: 'SBIN',
      exchange: 'NSE',
      mode: 3,
      data: { ltp: 120.5, timestamp: start, last_trade_time: start, volume: 9_000 },
    })
    // The phone's clock is off; the candle is not: by the exchange, 0.2 s before and 0.25 s after.
    await vi.advanceTimersByTimeAsync(29_800)
    expect(barsOf(chart).at(-1)?.time).toBe(boundary - step)
    await vi.advanceTimersByTimeAsync(450)
    expect(barsOf(chart).at(-1)).toMatchObject({
      time: boundary,
      open: 120.5,
      close: 120.5,
      volume: 0,
    })
    expectOneBarPerBucket(barsOf(chart))
  })

  it('opens the next candle on time after the phone slept, though a timer set before the sleep was still waiting', async () => {
    // A 1m chart. The page hides at 10:00:02, the clock's timer aimed at
    // 10:01:00; the phone sleeps to 10:13:30 and that timer still has 58 s to
    // run. History brings the chart up to 10:13 on the wake, and the moment
    // after 10:14:00 is quiet: the clock alone opens the 10:14 candle, and the
    // old timer would have opened it at 10:14:28.
    vi.setSystemTime(ist(10, 0, 1))
    answerHistory('1m', { close: 120.5 })
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    await vi.advanceTimersByTimeAsync(0)
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    socket.receive({
      type: 'market_data',
      symbol: 'SBIN',
      exchange: 'NSE',
      mode: 3,
      data: { ltp: 120.5, timestamp: ist(10, 0, 1), last_trade_time: ist(10, 0, 1), volume: 9_000 },
    })
    await vi.advanceTimersByTimeAsync(1_000)
    setVisibility('hidden')
    vi.setSystemTime(ist(10, 13, 30))
    setVisibility('visible')
    // The socket is alive: it answers the wake probe.
    await vi.advanceTimersByTimeAsync(100)
    socket.pong()
    await vi.advanceTimersByTimeAsync(ist(10, 14, 0.25) - Date.now())
    expect(barsOf(chart).at(-1)).toMatchObject({ time: sec(ist(10, 14)), close: 120.5, volume: 0 })
    expectOneBarPerBucket(barsOf(chart))
  })
})

describe('waking', () => {
  const OPTION = ['NIFTY27OCT2625900CE', 'NFO'] as const

  it('says Reconnecting at once, reads the whole gap, replaces the dead socket, and holds one candle a bucket', async () => {
    const tape = wakeGap as Tape
    // A liquid contract: the exchange traded through the sleep, and history knows it.
    const { chart, heard, socket: first } = await openAt(tape, ...OPTION, '1m', { close: 141 })
    const play = player(tape)
    await play.to(ist(10, 0, 45) - tape.start)
    expect(lineOf(heard)).toEqual({ kind: 'live' })
    const held = barsOf(chart).at(-1)?.time as number
    expect(held).toBe(sec(ist(10, 0)))
    const asked = vi.spyOn(OpenAlgoDataFeed.prototype, 'getBars')

    await play.to(ist(10, 12, 46) - tape.start)
    // Within the wake itself, before any timer: never the old price as current.
    expect(lineOf(heard)).toMatchObject({ kind: 'connecting' })
    await vi.advanceTimersByTimeAsync(0)
    expect(asked).toHaveBeenCalled()
    for (const [request] of asked.mock.calls) {
      expect(request.from).toBeLessThanOrEqual(held)
      expect(request.to).toBeGreaterThanOrEqual(sec(ist(10, 12, 46)))
    }

    // Nothing answers the probe: the socket is replaced within a second and a half.
    await play.to(ist(10, 12, 46) + PROBE_TIMEOUT_MS - tape.start)
    const second = FakeProxySocket.latest() as FakeProxySocket
    expect(second).not.toBe(first)
    expect(first.readyState).toBe(3)
    second.accept()
    expect(second.subscriptions()).toEqual([
      expect.objectContaining({ symbol: OPTION[0], exchange: 'NFO', mode: 3 }),
    ])
    await play.to(ist(10, 12, 49) - tape.start)
    expect(lineOf(heard)).toEqual({ kind: 'live' })
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 142.3, polled: false })

    await play.end()
    const bars = barsOf(chart)
    expectOneBarPerBucket(bars)
    // Every minute from the open to the one forming now, once.
    const minutes = (sec(ist(10, 13)) - sec(ist(9, 15))) / 60 + 1
    expect(bars.filter((bar) => bar.time >= sec(ist(9, 15)))).toHaveLength(minutes)
    expect(bars.at(-1)).toMatchObject({ time: sec(ist(10, 13)), close: 142.2 })
    expect(bars.find((bar) => bar.time === sec(ist(10, 12)))?.close).toBe(142.35)
  })

  it('holds one candle a bucket the same way on a page that was only hidden, its clock opening flat candles meanwhile', async () => {
    const tape = wakeGap as Tape
    const { chart, socket: first } = await openAt(tape, ...OPTION, '1m', { close: 141 })
    const play = player(tape, { asleep: false })
    await play.to(ist(10, 12, 45) - tape.start)
    // The clock went on opening candles nobody traded, flat at the last price.
    expect(barsOf(chart).at(-1)).toMatchObject({ time: sec(ist(10, 12)), close: 141, volume: 0 })
    await play.to(ist(10, 12, 46) + PROBE_TIMEOUT_MS - tape.start)
    expect(FakeProxySocket.latest()).not.toBe(first)
    ;(FakeProxySocket.latest() as FakeProxySocket).accept()
    await play.end()
    const bars = barsOf(chart)
    expectOneBarPerBucket(bars)
    // History's candles took the place of the flat ones the clock opened.
    expect(bars.find((bar) => bar.time === sec(ist(10, 6)))?.volume).toBe(1_000)
  })

  it('reads nothing while hidden, however long and whatever the socket does, and reads the gap when shown', async () => {
    answerHistory('1m', { close: 120.5 })
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    await vi.advanceTimersByTimeAsync(0)
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    socket.receive({
      type: 'market_data',
      symbol: 'SBIN',
      exchange: 'NSE',
      mode: 3,
      data: { ltp: 120.5, timestamp: Date.now(), last_trade_time: Date.now(), volume: 9_000 },
    })
    await vi.advanceTimersByTimeAsync(1_000)
    setVisibility('hidden')
    const before = host.calls.length
    // The screen is off; a minute later the network drops the socket, and the
    // reconnects behind it find nobody.
    await vi.advanceTimersByTimeAsync(60_000)
    socket.drop()
    await vi.advanceTimersByTimeAsync(11 * 60_000)
    expect(host.calls.slice(before).map((call) => call.path)).toEqual([])
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(0)
    expect(host.calls.slice(before).map((call) => call.path)).toContain('history')
    expectOneBarPerBucket(barsOf(chart))
  })

  it('opens no candle from the first frame after a wake past the close, and says the market is closed', async () => {
    vi.setSystemTime(ist(15, 20, 0))
    answerHistory('5m', { until: ist(15, 29, 59), close: 120.5 })
    const { chart, heard } = mountChart()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    const first = FakeProxySocket.latest() as FakeProxySocket
    first.accept()
    setVisibility('hidden')
    vi.setSystemTime(ist(15, 50, 0))
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(PROBE_TIMEOUT_MS)
    const second = FakeProxySocket.latest() as FakeProxySocket
    expect(second).not.toBe(first)
    second.accept()
    // The broker's current state: the day's last trade, in a frame stamped now.
    second.receive(
      depthFrame('SBIN', 'NSE', {
        ltp: 121.05,
        at: Date.now(),
        tradeAt: ist(15, 29, 59.8),
        volume: 1_250_000,
        prevClose: 118,
      })
    )
    await vi.advanceTimersByTimeAsync(0)
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 121.05, polled: false })
    expect(barsOf(chart).some((bar) => bar.time >= sec(ist(15, 30)))).toBe(false)
    expect(lineOf(heard)).toMatchObject({ kind: 'closed' })
    expectOneBarPerBucket(barsOf(chart))
  })

  it('replaces a socket that stays silent after the wake within a second and a half, and not before', async () => {
    const tape = wakeSilent as Tape
    const { socket } = await openAt(tape, 'SBIN', 'NSE', '5m')
    const play = player(tape)
    await play.to(ist(10, 0, 46) - tape.start)
    expect(socket.pings().length).toBeGreaterThan(0)
    await play.to(ist(10, 0, 46) + PROBE_TIMEOUT_MS - 100 - tape.start)
    expect(FakeProxySocket.latest()).toBe(socket)
    await play.to(ist(10, 0, 46) + PROBE_TIMEOUT_MS - tape.start)
    expect(FakeProxySocket.latest()).not.toBe(socket)
  })

  it.each([
    ['1m', 12 * 60],
    ['3m', 12 * 60],
    ['5m', 25 * 60],
    ['15m', 45 * 60],
    ['1h', 3 * 3600],
    ['1D', 3 * 86_400],
  ] as const)('reads the gap from the last candle it holds to now on %s', async (chip, awaySec) => {
    answerHistory(chip)
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', chip)
    await vi.advanceTimersByTimeAsync(0)
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    const held = barsOf(chart).at(-1)?.time as number
    const asked = vi.spyOn(OpenAlgoDataFeed.prototype, 'getBars')
    setVisibility('hidden')
    vi.setSystemTime(Date.now() + awaySec * 1000)
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(0)
    expect(asked).toHaveBeenCalled()
    for (const [request] of asked.mock.calls) {
      expect(request.from).toBeLessThanOrEqual(held)
      expect(request.to).toBeGreaterThanOrEqual(sec(Date.now()) - 1)
    }
    socket.pong()
    await vi.advanceTimersByTimeAsync(PROBE_TIMEOUT_MS)
    const bars = barsOf(chart)
    expectOneBarPerBucket(bars)
    expect(bars.at(-1)?.time).toBeGreaterThan(held)
  })
})
