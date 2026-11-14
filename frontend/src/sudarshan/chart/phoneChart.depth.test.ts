/**
 * The book and open interest on the phone chart (M4.15, C9), from the chart's
 * own depth stream on the real engine: every depth frame reaches the page,
 * and a contract's open interest rides on each price. And the price under a
 * held finger (C10), which a ticket opened from a candle reading starts at.
 */
import { type Chart, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ist } from '@/test/tapes/scenarios'
import { depthFrame, FakeProxySocket } from '@/test/tickTape'
import type { PhoneChart } from './phoneChart'
import { historyOf, host, installHost, mountChart, removeHost } from './testing/host'

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

const CONTRACT = 'NIFTY27OCT2625900CE'

beforeEach(() => {
  installHost(ist(10, 0, 30))
  host.replies.history = () =>
    historyOf([
      { time: ist(10, 0) / 1000, open: 140, high: 141, low: 139.5, close: 140.5, volume: 650 },
    ])
})
afterEach(() => removeHost())

describe('the book and open interest', () => {
  it('hands the page every depth frame, five levels a side, and the open interest on each price', async () => {
    const { chart, heard } = mountChart()
    await chart.load(CONTRACT, 'NFO', '5m')
    await vi.advanceTimersByTimeAsync(0)
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    expect(socket.subscriptions()).toEqual([
      expect.objectContaining({ symbol: CONTRACT, mode: 3, depth: 5 }),
    ])
    socket.receive(
      depthFrame(CONTRACT, 'NFO', {
        ltp: 142.3,
        at: ist(10, 0, 31),
        tradeAt: ist(10, 0, 30.8),
        volume: 61_000,
        oi: 1_206_500,
        bid: { price: 142.25, quantity: 650 },
        ask: { price: 142.3, quantity: 1_300 },
      })
    )
    expect(heard.depths.at(-1)).toMatchObject({
      bids: [
        expect.objectContaining({ price: 142.25, qty: 650 }),
        expect.anything(),
        expect.anything(),
        expect.anything(),
        expect.anything(),
      ],
      asks: [
        expect.objectContaining({ price: 142.3, qty: 1_300 }),
        expect.anything(),
        expect.anything(),
        expect.anything(),
        expect.anything(),
      ],
    })
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 142.3, oi: 1_206_500, polled: false })
  })
})

describe('the price under a held finger (C10)', () => {
  const engine = (chart: PhoneChart) => (chart as unknown as { chart: Chart }).chart

  it("reads the candles' pane at the finger's height, on the contract's tick", async () => {
    host.replies.history = () =>
      historyOf(
        Array.from({ length: 9 }, (_, i) => ({
          time: ist(9, 15 + i * 5) / 1000,
          open: 140 + i,
          high: 141.35 + i,
          low: 139.2 + i,
          close: 140.6 + i,
          volume: 650 * (i + 1),
        }))
      )
    const { chart } = mountChart()
    await chart.load(CONTRACT, 'NFO', '5m')
    chart.setTick(0.05)
    await vi.advanceTimersByTimeAsync(0)
    const pane = engine(chart).plotRect(0)
    if (!pane) throw new Error('the candles have no pane')
    for (const y of [pane.top + 4, pane.top + pane.height / 2, pane.top + pane.height - 4]) {
      const price = chart.priceAt(y)
      expect(price).not.toBeNull()
      // On the tick: a whole number of 0.05 steps.
      expect(
        Math.abs((price as number) / 0.05 - Math.round((price as number) / 0.05))
      ).toBeLessThan(1e-6)
      // Where the finger is: the price drawn at its height, within half a tick's height.
      const back = engine(chart).priceToCoordinate(price as number, 0) as number
      const tickHeight = Math.abs(
        (engine(chart).priceToCoordinate((price as number) + 0.05, 0) as number) - back
      )
      expect(Math.abs(back - y)).toBeLessThanOrEqual(tickHeight / 2 + 0.5)
    }
    // Higher on the screen is a higher price.
    expect(chart.priceAt(pane.top + 4) as number).toBeGreaterThan(
      chart.priceAt(pane.top + pane.height - 4) as number
    )
  })

  it('reads nothing over the volume pane or below the candles, where no price of it is drawn', async () => {
    const { chart } = mountChart()
    await chart.load(CONTRACT, 'NFO', '5m')
    await vi.advanceTimersByTimeAsync(0)
    const volume = engine(chart).plotRect(1)
    if (!volume) throw new Error('the contract has no volume pane')
    expect(chart.priceAt(volume.top + volume.height / 2)).toBeNull()
    expect(chart.priceAt(10_000)).toBeNull()
    expect(chart.priceAt(-5)).toBeNull()
  })
})
