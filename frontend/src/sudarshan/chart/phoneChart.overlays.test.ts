/**
 * The overlays seam (M4.7, C5) on the real chart engine: lines and marks set
 * once are drawn, kept through every chart the host builds, untouched by a wake
 * reconnect, and never draggable.
 */
import { type Bar, type Chart, type PriceLine, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PROBE_TIMEOUT_MS } from '@/lib/trading/liveTicks'
import { ist } from '@/test/tapes/scenarios'
import { depthFrame, FakeProxySocket } from '@/test/tickTape'
import type { OverlayLine } from './overlays'
import type { PhoneChart } from './phoneChart'
import { historyOf, host, installHost, mountChart, removeHost, setVisibility } from './testing/host'

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

const bars = (): Bar[] =>
  Array.from({ length: 60 }, (_, i) => ({
    time: ist(9, 15) / 1000 - 86_400 + i * 300,
    open: 800 + i * 0.5,
    high: 802 + i * 0.5,
    low: 799 + i * 0.5,
    close: 801 + i * 0.5,
    volume: 1_000,
  }))

const LINES: OverlayLine[] = [
  { id: 'position:MIS', kind: 'position', price: 815, side: 'BUY', label: 'Buy 65 @ 815.00' },
  { id: 'order:A1:trigger', kind: 'order', price: 805, side: 'SELL', label: 'SELL 65 SL-M 805.00' },
  { id: 'alert:7', kind: 'alert', price: 825, side: null, label: 'Alert ≥ 825.00' },
]

const engine = (chart: PhoneChart) => (chart as unknown as { chart: Chart }).chart
const drawn = (chart: PhoneChart) =>
  (chart as unknown as { lineApis: Map<string, PriceLine> }).lineApis

beforeEach(() => {
  installHost(ist(10, 0, 30))
  host.replies.history = () => historyOf(bars())
})
afterEach(() => removeHost())

async function opened() {
  const view = mountChart()
  view.chart.setOverlays({ lines: LINES })
  await view.chart.load('SBIN', 'NSE', '5m')
  await vi.advanceTimersByTimeAsync(0)
  return view
}

describe('lines over the phone chart', () => {
  it('draws each line at its price, a position solid, an order dashed, an alert dotted', async () => {
    const { chart } = await opened()
    const options = [...drawn(chart).values()].map((line) => line.options())
    expect(options.map((o) => [o.id, o.price, o.lineStyle])).toEqual([
      ['position:MIS', 815, 'solid'],
      ['order:A1:trigger', 805, 'dashed'],
      ['alert:7', 825, 'dotted'],
    ])
  })

  it('never makes a line draggable: no drag cursor, no close button, no drag subscription', async () => {
    const drag = vi.fn()
    const view = mountChart()
    vi.spyOn(Object.getPrototypeOf(engine(view.chart)), 'subscribeDrag').mockImplementation(drag)
    view.chart.setOverlays({ lines: LINES })
    await view.chart.load('SBIN', 'NSE', '5m')
    for (const line of drawn(view.chart).values()) {
      expect(line.options().cursor).toBeUndefined()
      expect(line.options().closeButton).toBeFalsy()
    }
    expect(drag).not.toHaveBeenCalled()
  })

  it('moves a line whose price changed, in place, and takes away one that is gone', async () => {
    const { chart } = await opened()
    const position = drawn(chart).get('position:MIS')
    chart.setOverlays({
      lines: [{ ...LINES[0], price: 816.5 }, LINES[2]],
    })
    expect(drawn(chart).get('position:MIS')).toBe(position)
    expect(position?.price).toBe(816.5)
    expect([...drawn(chart).keys()]).toEqual(['position:MIS', 'alert:7'])
  })

  it('colours a line by its side from the D tokens, and a stale one grey', async () => {
    const { chart } = await opened()
    const colour = (id: string) => drawn(chart).get(id)?.options().color
    expect(colour('position:MIS')).toBe('var(--buy-solid)')
    expect(colour('order:A1:trigger')).toBe('var(--sell-solid)')
    expect(colour('alert:7')).toBe('var(--info)')
    chart.setOverlays({ lines: [{ ...LINES[0], stale: true }] })
    expect(colour('position:MIS')).toBe('var(--stale)')
  })

  it('puts the lines back on every chart it builds: another timeframe, another style, Analyze', async () => {
    const { chart } = await opened()
    const first = engine(chart)
    await chart.load('SBIN', 'NSE', '15m')
    expect(engine(chart)).not.toBe(first)
    expect([...drawn(chart).keys()]).toEqual(LINES.map((line) => line.id))
    await chart.setStyle('line')
    expect([...drawn(chart).keys()]).toEqual(LINES.map((line) => line.id))
    chart.setAppMode('analyzer')
    expect([...drawn(chart).keys()]).toEqual(LINES.map((line) => line.id))
  })

  it('leaves the lines untouched across a wake reconnect, which builds nothing anew', async () => {
    const { chart } = await opened()
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    const before = [...drawn(chart).values()]
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(10_000)
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(PROBE_TIMEOUT_MS)
    const replaced = FakeProxySocket.latest() as FakeProxySocket
    expect(replaced).not.toBe(socket)
    replaced.accept()
    replaced.receive(depthFrame('SBIN', 'NSE', { ltp: 830, at: Date.now(), tradeAt: Date.now() }))
    expect([...drawn(chart).values()]).toEqual(before)
    for (const [i, line] of [...drawn(chart).values()].entries()) expect(line).toBe(before[i])
  })

  it('says where each line in view stands, and tells its readers when one moves', async () => {
    const { chart } = await opened()
    const heard = vi.fn()
    chart.onOverlayLayout(heard)
    const layout = chart.overlayLayout()
    expect(layout.map((line) => line.id).sort()).toEqual(LINES.map((line) => line.id).sort())
    // A higher price is higher on the screen.
    const y = (id: string) => chart.overlayLayout().find((line) => line.id === id)?.y ?? Number.NaN
    expect(y('alert:7')).toBeLessThan(y('position:MIS'))
    expect(y('position:MIS')).toBeLessThan(y('order:A1:trigger'))
    chart.setOverlays({ lines: [{ ...LINES[0], price: 820 }, LINES[1], LINES[2]] })
    expect(heard).toHaveBeenCalled()
    expect(y('position:MIS')).not.toBe(layout.find((line) => line.id === 'position:MIS')?.y)
  })

  it('keeps the two halves apart: marks set later leave the lines, and an empty half clears', async () => {
    const { chart } = await opened()
    const time = bars()[40].time
    chart.setOverlays({ markers: [{ id: 't1', time, side: 'BUY', price: 820, text: 'B 65' }] })
    expect([...drawn(chart).keys()]).toEqual(LINES.map((line) => line.id))
    const layer = (chart as unknown as { markerLayer: { _markers: unknown[] } }).markerLayer
    expect(layer._markers).toEqual([
      expect.objectContaining({
        id: 't1',
        time,
        shape: 'arrowUp',
        position: 'atPrice',
        price: 820,
      }),
    ])
    chart.setOverlays({ lines: [] })
    expect(drawn(chart).size).toBe(0)
    expect(chart.overlayLayout()).toEqual([])
    // The marks are put back on the next chart too.
    await chart.load('SBIN', 'NSE', '15m')
    const next = (chart as unknown as { markerLayer: { _markers: unknown[] } }).markerLayer
    expect(next._markers).toHaveLength(1)
  })
})
