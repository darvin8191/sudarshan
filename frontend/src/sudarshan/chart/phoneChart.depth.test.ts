/**
 * The book and open interest on the phone chart (M4.15, C9), from the chart's
 * own depth stream on the real engine: every depth frame reaches the page,
 * and a contract's open interest rides on each price.
 */
import { SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ist } from '@/test/tapes/scenarios'
import { depthFrame, FakeProxySocket } from '@/test/tickTape'
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
