/**
 * The phone chart's styles (M4.15, C2) on the real chart engine: another
 * style keeps the candles and the view, and Heikin Ashi is the engine's own
 * transform, its tier loaded the first time it is chosen.
 */
import { type Bar, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ist } from '@/test/tapes/scenarios'
import { FakeProxySocket } from '@/test/tickTape'
import type { PhoneChart } from './phoneChart'
import { barsOf, historyOf, host, installHost, mountChart, removeHost } from './testing/host'

vi.mock('@/lib/trading/sessionHours', () => ({
  applySessionHours: () => {},
  sessionCalendarFor: async () =>
    new SessionCalendar({
      timezone: 'Asia/Kolkata',
      sessions: ['0915-1530:23456'],
      exceptions: {},
    }),
}))
vi.mock('@/lib/trading/previousClose', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/previousClose')>()),
  previousClose: async () => null,
}))

/** Ten five-minute candles from 09:15, rising. */
const bars = (): Bar[] =>
  Array.from({ length: 10 }, (_, i) => ({
    time: ist(9, 15 + i * 5) / 1000,
    open: 100 + i,
    high: 101.5 + i,
    low: 99.5 + i,
    close: 101 + i,
    volume: 1_000 + i,
  }))

const engine = (chart: PhoneChart) =>
  (chart as unknown as { chart: import('openalgo-charts').Chart; price: unknown }).chart
const priceSeries = (chart: PhoneChart) =>
  (chart as unknown as { price: import('openalgo-charts').SeriesApi }).price

beforeEach(() => {
  installHost(ist(10, 0, 30))
  host.replies.history = () => historyOf(bars())
})
afterEach(() => removeHost())

describe('chart styles', () => {
  it('redraws a line with the same candles and view', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '5m')
    const held = [...barsOf(chart)]
    await chart.setStyle('line')
    expect(barsOf(chart)).toEqual(held)
    expect(engine(chart).seriesTransform(priceSeries(chart))).toBeNull()
  })

  it("draws Heikin Ashi through the engine's transform, and reads its own candle under a finger", async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '5m')
    await chart.setStyle('heikin-ashi')
    expect(engine(chart).seriesTransform(priceSeries(chart))).toEqual({ type: 'heikin-ashi' })
    // The candles held stay the exchange's; what is drawn is the transform's.
    expect(barsOf(chart)).toEqual(bars())
    const drawn = engine(chart).primaryBars()
    expect(drawn).toHaveLength(10)
    const first = bars()[0]
    expect(drawn[0].close).toBeCloseTo((first.open + first.high + first.low + first.close) / 4)
    const x = engine(chart).timeScale.indexToX(9)
    const read = chart.barAt(x)
    expect(read?.time).toBe(bars()[9].time)
    expect(read?.close).toBeCloseTo(drawn[9].close)
    expect(read?.volume).toBe(1_009)
  })

  it('opens on a Heikin Ashi chosen before, once its tier is in, and moves its candle on a tick', async () => {
    const { chart } = mountChart({ style: 'heikin-ashi' })
    await chart.load('SBIN', 'NSE', '5m')
    expect(engine(chart).seriesTransform(priceSeries(chart))).toEqual({ type: 'heikin-ashi' })
    await vi.advanceTimersByTimeAsync(0)
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    socket.receive({
      type: 'market_data',
      symbol: 'SBIN',
      exchange: 'NSE',
      mode: 3,
      data: { ltp: 115, timestamp: ist(10, 0, 31), last_trade_time: ist(10, 0, 31), volume: 9_000 },
    })
    expect(barsOf(chart).at(-1)).toMatchObject({ time: ist(10, 0) / 1000, close: 115 })
    expect(engine(chart).primaryBars().at(-1)?.high).toBeGreaterThanOrEqual(115)
  })
})
