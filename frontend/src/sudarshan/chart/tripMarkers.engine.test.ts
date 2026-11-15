/**
 * A trade's marks on the real chart engine (M14.8): drawn as triangles in the side's colour,
 * under a buy and over a sell, a size up from the plain marks.
 */
import { type Bar, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ist } from '@/test/tapes/scenarios'
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
// The token asked for, as written: jsdom has no stylesheet to resolve it through.
vi.mock('@/lib/trading/chartTheme', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/chartTheme')>()),
  resolveCssColor: (colour: string) => colour,
}))
vi.mock('@/lib/trading/previousClose', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/previousClose')>()),
  previousClose: async () => null,
}))

/** Minute candles of today from 09:15. */
function minutes(count = 300): Bar[] {
  return Array.from({ length: count }, (_, i) => ({
    time: ist(9, 15) / 1000 + i * 60,
    open: 800 + i * 0.05,
    high: 801 + i * 0.05,
    low: 799 + i * 0.05,
    close: 800.5 + i * 0.05,
    volume: 1_000,
  }))
}

type Layer = { _markers: Array<Record<string, unknown>> }
const layer = (chart: PhoneChart) => (chart as unknown as { markerLayer: Layer | null }).markerLayer

beforeEach(() => {
  installHost(ist(13, 0))
  host.replies.history = () => historyOf(minutes())
})
afterEach(() => removeHost())

describe('the trade marks on the engine', () => {
  it('draws a trade mark as a triangle in the side colour, under a buy and over a sell', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    const [a, b] = [barsOf(chart)[10].time, barsOf(chart)[20].time]
    chart.setOverlays({
      markers: [
        { id: 'e', time: a, side: 'BUY', shape: 'triangle', text: 'B 803.05' },
        { id: 'x', time: b, side: 'SELL', shape: 'triangle', text: 'S' },
      ],
    })
    expect(layer(chart)?._markers).toEqual([
      expect.objectContaining({
        id: 'e',
        time: a,
        shape: 'triangleUp',
        position: 'belowBar',
        size: 'medium',
        color: 'var(--buy-solid)',
        text: 'B 803.05',
      }),
      expect.objectContaining({
        id: 'x',
        time: b,
        shape: 'triangleDown',
        position: 'aboveBar',
        size: 'medium',
        color: 'var(--sell-solid)',
        text: 'S',
      }),
    ])
  })

  it('keeps the plain marks as they were: arrows, a size down', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    const time = barsOf(chart)[10].time
    chart.setOverlays({ markers: [{ id: 'p', time, side: 'SELL', text: 'S' }] })
    expect(layer(chart)?._markers).toEqual([
      expect.objectContaining({ shape: 'arrowDown', size: 'small', position: 'aboveBar' }),
    ])
  })
})
