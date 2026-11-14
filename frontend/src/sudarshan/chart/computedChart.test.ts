/**
 * The computed chart's host (M4.13, C11), with the real chart engine and the real live-bar
 * pipeline; only canvas painting, the network and the market calendar's source are replaced.
 * It opens no socket: the screen hands each leg's price over.
 */
import { type Bar, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartStateView } from '@/lib/trading/chartState'
import { FakeProxySocket } from '@/test/tickTape'
import type { CombinedTick, ComputedSpec } from './computed'
import { ComputedChart } from './computedChart'
import { historyOf, host, installHost, removeHost, setVisibility } from './testing/host'

vi.mock('@/lib/trading/sessionHours', () => ({
  applySessionHours: () => {},
  sessionCalendarFor: async () =>
    new SessionCalendar({ timezone: 'Asia/Kolkata', sessions: ['0915-1530:23456'] }),
}))

/** UTC seconds for a time of day in India on Tuesday 20 October 2026, a trading day. */
const ist = (h: number, mi: number, s = 0) => Date.UTC(2026, 9, 20, h, mi, s) / 1000 - 19_800
const CE = { symbol: 'NIFTY27OCT2625900CE', exchange: 'NFO' }
const PE = { symbol: 'NIFTY27OCT2625900PE', exchange: 'NFO' }
const spec: ComputedSpec = { legs: [CE, PE], op: 'sum' }

/** Five-minute candles from 09:15 up to the one forming at `until`, closing at `close`. */
function legBars(until: number, close: number): Bar[] {
  const bars: Bar[] = []
  for (let t = ist(9, 15); t <= until; t += 300)
    bars.push({ time: t, open: close, high: close + 2, low: close - 2, close, volume: 500 })
  return bars
}

/** History as the exchange has it at the moment it is read: the call at 140, the put at 90. */
function answerHistory(prices: Record<string, number> = { [CE.symbol]: 140, [PE.symbol]: 90 }) {
  host.replies.history = (body) => {
    const close = prices[String(body.symbol)]
    return close === undefined
      ? { status: 'success', data: [] }
      : historyOf(legBars(Math.floor(Date.now() / 1000), close))
  }
}

const charts: ComputedChart[] = []
function mount() {
  const heard = { loads: [] as ChartStateView[], values: [] as CombinedTick[] }
  const container = document.createElement('div')
  Object.defineProperty(container, 'clientWidth', { configurable: true, value: 328 })
  Object.defineProperty(container, 'clientHeight', { configurable: true, value: 260 })
  document.body.appendChild(container)
  const chart = new ComputedChart({
    container,
    apiKey: 'test-key',
    appMode: 'live',
    name: 'NIFTY 25,900 straddle',
    callbacks: {
      onLoad: (state) => heard.loads.push(state),
      onValue: (tick) => heard.values.push(tick),
    },
  })
  charts.push(chart)
  return { chart, heard }
}
const barsOf = (chart: ComputedChart): Bar[] => (chart as unknown as { bars: Bar[] }).bars
const historyCalls = () => host.calls.filter((call) => call.path === 'history')

beforeEach(() => {
  installHost(ist(10, 2, 0) * 1000)
  answerHistory()
})
afterEach(() => {
  for (const chart of charts.splice(0)) chart.destroy()
  removeHost()
})

describe('ComputedChart.loadComputed', () => {
  it("draws the sum of the two legs' candles, one read a leg", async () => {
    const { chart, heard } = mount()
    await chart.loadComputed(spec, '5m')
    expect(heard.loads.at(-1)?.kind).toBe('ready')
    const bars = barsOf(chart)
    expect(bars.at(-1)).toMatchObject({ time: ist(10, 0), close: 230 })
    expect(bars).toHaveLength(10)
    expect(historyCalls().map((call) => call.body.symbol)).toEqual([CE.symbol, PE.symbol])
    expect(historyCalls().every((call) => call.body.interval === '5m')).toBe(true)
  })

  it('opens no socket and subscribes to nothing: it adds no instrument to the feed', async () => {
    const { chart } = mount()
    await chart.loadComputed(spec, '5m')
    chart.tick(0, 141, ist(10, 2, 5))
    await vi.advanceTimersByTimeAsync(400_000)
    expect(FakeProxySocket.all).toHaveLength(0)
  })

  it('moves the forming bar on a tick of either leg, the other at its last price', async () => {
    const { chart, heard } = mount()
    await chart.loadComputed(spec, '5m')
    chart.tick(0, 143, ist(10, 2, 10))
    expect(barsOf(chart).at(-1)).toMatchObject({ time: ist(10, 0), close: 233, high: 233 })
    chart.tick(1, 86, ist(10, 2, 11))
    expect(barsOf(chart).at(-1)).toMatchObject({ time: ist(10, 0), close: 229, high: 233 })
    expect(heard.values.at(-1)).toEqual({ price: 229, time: ist(10, 2, 11) })
    expect(barsOf(chart)).toHaveLength(10)
  })

  it('opens the next candle on time though neither leg trades', async () => {
    const { chart } = mount()
    await chart.loadComputed(spec, '5m')
    chart.tick(0, 142, ist(10, 2, 30))
    await vi.advanceTimersByTimeAsync(3 * 60_000 + 2_000)
    const last = barsOf(chart).at(-1)
    expect(last?.time).toBe(ist(10, 5))
    expect(last).toMatchObject({ open: 232, close: 232 })
  })

  it('puts a tick stamped in the next bucket into its own candle', async () => {
    const { chart } = mount()
    await chart.loadComputed(spec, '5m')
    vi.setSystemTime(ist(10, 5, 1) * 1000)
    chart.tick(1, 95, ist(10, 5, 1))
    const bars = barsOf(chart)
    expect(bars.at(-1)).toMatchObject({ time: ist(10, 5), open: 235, close: 235 })
    expect(bars.at(-2)).toMatchObject({ time: ist(10, 0), close: 230 })
  })

  it('says a side has no candles in a sentence, and draws nothing', async () => {
    answerHistory({ [CE.symbol]: 140 })
    const { chart, heard } = mount()
    await chart.loadComputed(spec, '5m')
    const state = heard.loads.at(-1)
    expect(state?.kind).toBe('error')
    expect(state?.message).toMatch(/No candles were returned for NIFTY27OCT2625900PE/)
    expect(state?.message).not.toMatch(/\(\d{3}\)|\/api|failed/i)
    expect(barsOf(chart)).toHaveLength(0)
  })

  it('refuses one leg or three with a sentence, without a read', async () => {
    const { chart, heard } = mount()
    await chart.loadComputed({ legs: [CE], op: 'sum' }, '5m')
    expect(heard.loads.at(-1)).toMatchObject({ kind: 'empty' })
    expect(heard.loads.at(-1)?.message).toMatch(/needs two/)
    await chart.loadComputed({ legs: [CE, PE, CE], op: 'sum' }, '5m')
    expect(heard.loads.at(-1)?.message).toMatch(/at most 2/)
    expect(historyCalls()).toHaveLength(0)
    chart.tick(0, 100, null) // nothing loaded: a tick is dropped
    expect(heard.values).toHaveLength(0)
  })

  it('re-points to another pair: the first pair’s late answer and ticks are dropped', async () => {
    const other: ComputedSpec = {
      legs: [
        { symbol: 'NIFTY27OCT2625950CE', exchange: 'NFO' },
        { symbol: 'NIFTY27OCT2625950PE', exchange: 'NFO' },
      ],
      op: 'sum',
    }
    answerHistory({
      [CE.symbol]: 140,
      [PE.symbol]: 90,
      NIFTY27OCT2625950CE: 110,
      NIFTY27OCT2625950PE: 115,
    })
    const { chart, heard } = mount()
    const first = chart.loadComputed(spec, '5m')
    const second = chart.loadComputed(other, '5m')
    await Promise.all([first, second])
    expect(barsOf(chart).at(-1)?.close).toBe(225)
    expect(heard.loads.at(-1)?.kind).toBe('ready')
    chart.tick(0, 111, ist(10, 2, 20))
    expect(barsOf(chart).at(-1)?.close).toBe(226)
  })

  it('reads another interval for both legs and keeps one candle a bucket', async () => {
    host.replies.history = (body) =>
      historyOf(
        legBars(Math.floor(Date.now() / 1000), body.symbol === CE.symbol ? 140 : 90).filter(
          (bar) => body.interval === '5m' || (bar.time - ist(9, 15)) % 900 === 0
        )
      )
    const { chart } = mount()
    await chart.loadComputed(spec, '15m')
    expect(historyCalls().every((call) => call.body.interval === '15m')).toBe(true)
    chart.tick(0, 150, ist(10, 2, 0))
    const times = barsOf(chart).map((bar) => bar.time)
    expect(new Set(times).size).toBe(times.length)
    expect(times.at(-1)).toBe(ist(10, 0))
  })
})

describe('ComputedChart after the phone sleeps', () => {
  it('reads nothing while hidden, and the gap on coming back', async () => {
    const { chart } = mount()
    await chart.loadComputed(spec, '5m')
    const before = historyCalls().length
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(12 * 60_000)
    expect(historyCalls()).toHaveLength(before)
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(1_000)
    expect(historyCalls().length).toBeGreaterThan(before)
    const bars = barsOf(chart)
    const times = bars.map((bar) => bar.time)
    expect(new Set(times).size).toBe(times.length)
    expect(times.at(-1)).toBe(ist(10, 10))
    expect(times).toContain(ist(10, 5))
  })

  it("reads the tail again when the screen's connection comes back", async () => {
    const { chart } = mount()
    await chart.loadComputed(spec, '5m')
    const before = historyCalls().length
    chart.resync()
    await vi.advanceTimersByTimeAsync(500)
    expect(historyCalls().length).toBeGreaterThan(before)
  })

  it('stops for good on destroy: no timer, no read, no tick', async () => {
    const { chart, heard } = mount()
    await chart.loadComputed(spec, '5m')
    chart.destroy()
    const before = historyCalls().length
    chart.tick(0, 150, ist(10, 3))
    chart.resync()
    await vi.advanceTimersByTimeAsync(20 * 60_000)
    expect(historyCalls()).toHaveLength(before)
    expect(heard.values).toHaveLength(0)
  })
})
