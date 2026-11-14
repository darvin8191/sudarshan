/**
 * Drawings on the phone chart (M4.3, C7) on the real chart engine and its real
 * draw tier: saved per instrument, swapped between the charts the host builds,
 * undone a step at a time, a ruler never kept, and the tier fetched only when
 * a tool or a saved drawing needs it.
 */
import { type Bar, SessionCalendar } from 'openalgo-charts'
import type { DrawingController } from 'openalgo-charts/draw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ist } from '@/test/tapes/scenarios'
import type { PhoneChart } from './phoneChart'
import { DRAW_TOOLS, hasSavedDrawings, phoneDrawingsKey } from './phoneDrawings'
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

const bars = (): Bar[] =>
  Array.from({ length: 60 }, (_, i) => ({
    time: ist(9, 15) / 1000 - 86_400 + i * 300,
    open: 800 + i * 0.5,
    high: 802 + i * 0.5,
    low: 799 + i * 0.5,
    close: 801 + i * 0.5,
    volume: 1_000,
  }))

const controller = (chart: PhoneChart) =>
  (chart.drawings as unknown as { ctl: DrawingController | null }).ctl
const tierOf = (chart: PhoneChart) => (chart.drawings as unknown as { tier: unknown }).tier

/** Place a level as the tool would: one drawing on the candles' pane. */
function level(chart: PhoneChart, price: number) {
  return (controller(chart) as DrawingController).add({
    tool: 'horizontal-line',
    points: [{ time: bars()[30].time, price }],
    style: {},
    paneIndex: 0,
  })
}
const saved = (symbol: string, exchange = 'NSE') =>
  JSON.parse(localStorage.getItem(phoneDrawingsKey(symbol, exchange) ?? '') ?? 'null') as {
    drawings: Array<{ tool: string; points: Array<{ price: number }> }>
  } | null

beforeEach(() => {
  localStorage.clear()
  installHost(ist(10, 0, 30))
  host.replies.history = () => historyOf(bars())
})
afterEach(() => removeHost())

async function opened(symbol = 'SBIN', exchange = 'NSE') {
  const view = mountChart()
  await view.chart.load(symbol, exchange, '5m')
  await vi.advanceTimersByTimeAsync(0)
  return view
}

describe('drawings on the phone chart', () => {
  it('offers six tools and a ruler, each one the engine knows', async () => {
    expect(DRAW_TOOLS.map((tool) => tool.label)).toEqual([
      'Horizontal line',
      'Trend line',
      'Ray',
      'Rectangle',
      'Fib retracement',
      'Text note',
      'Measure',
    ])
    const { hasDrawingTool, registerBuiltinDrawingTools } = await import('openalgo-charts/draw')
    registerBuiltinDrawingTools()
    for (const tool of DRAW_TOOLS) expect(hasDrawingTool(tool.id), tool.id).toBe(true)
  })

  it('does not fetch the draw tier for a chart nobody draws on', async () => {
    const { chart } = await opened()
    expect(tierOf(chart)).toBeNull()
    expect(controller(chart)).toBeNull()
    expect(chart.drawings.state()).toEqual({ tool: null, count: 0, canUndo: false, failed: false })
  })

  it('holds a tool on the first use, and lets it go again', async () => {
    const { chart } = await opened()
    const heard = vi.fn()
    chart.drawings.subscribe(heard)
    await chart.drawings.setTool('trend-line')
    expect(chart.drawings.state().tool).toBe('trend-line')
    expect(controller(chart)?.magnetMode()).toBe('weak')
    expect(heard).toHaveBeenCalled()
    chart.drawings.cancel()
    expect(chart.drawings.state().tool).toBeNull()
  })

  it("saves a drawing under its instrument's own key, in the phone chart's namespace", async () => {
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    level(chart, 815)
    expect(phoneDrawingsKey('SBIN', 'NSE')).toBe('sudarshan-chart-draw:NSE:SBIN')
    expect(saved('SBIN')?.drawings.map((d) => [d.tool, d.points[0].price])).toEqual([
      ['horizontal-line', 815],
    ])
    expect(hasSavedDrawings('SBIN', 'NSE')).toBe(true)
    expect(hasSavedDrawings('INFY', 'NSE')).toBe(false)
    expect(chart.drawings.state().count).toBe(1)
  })

  it('swaps the drawings between two charts: another instrument starts clean, and the first comes back', async () => {
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    level(chart, 815)
    await chart.load('INFY', 'NSE', '5m')
    expect(controller(chart)?.drawings()).toEqual([])
    level(chart, 1500)
    expect(saved('SBIN')?.drawings).toHaveLength(1)
    expect(saved('INFY')?.drawings[0].points[0].price).toBe(1500)
    await chart.load('SBIN', 'NSE', '5m')
    expect(
      controller(chart)
        ?.drawings()
        .map((d) => d.points[0].price)
    ).toEqual([815])
  })

  it('keeps the drawings through another timeframe, another style and Analyze mode', async () => {
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    level(chart, 815)
    const first = controller(chart)
    await chart.load('SBIN', 'NSE', '15m')
    expect(controller(chart)).not.toBe(first)
    expect(controller(chart)?.drawings()).toHaveLength(1)
    await chart.setStyle('line')
    expect(controller(chart)?.drawings()).toHaveLength(1)
    chart.setAppMode('analyzer')
    expect(controller(chart)?.drawings()).toHaveLength(1)
  })

  it('fetches the tier by itself for an instrument that has drawings saved', async () => {
    localStorage.setItem(
      phoneDrawingsKey('SBIN', 'NSE') ?? '',
      JSON.stringify({
        version: 2,
        drawings: [
          {
            id: 'd1',
            tool: 'horizontal-line',
            points: [{ time: bars()[10].time, price: 810 }],
            style: {},
            paneIndex: 0,
            zIndex: 0,
          },
        ],
      })
    )
    const { chart } = await opened()
    await vi.waitFor(() => expect(controller(chart)?.drawings()).toHaveLength(1))
    expect(chart.drawings.state().count).toBe(1)
    // Restoring is not a step to undo.
    expect(chart.drawings.state().canUndo).toBe(false)
  })

  it('takes back the last step, and saves what is left', async () => {
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    level(chart, 815)
    level(chart, 820)
    expect(chart.drawings.state()).toMatchObject({ count: 2, canUndo: true })
    chart.drawings.undo()
    expect(chart.drawings.state().count).toBe(1)
    expect(saved('SBIN')?.drawings.map((d) => d.points[0].price)).toEqual([815])
    chart.drawings.undo()
    expect(chart.drawings.state()).toMatchObject({ count: 0, canUndo: false })
    expect(saved('SBIN')?.drawings).toEqual([])
  })

  it('never keeps a ruler: not counted, not saved, gone with the next tool', async () => {
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    level(chart, 815)
    controller(chart)?.add({
      tool: 'measure',
      points: [
        { time: bars()[10].time, price: 805 },
        { time: bars()[20].time, price: 812 },
      ],
      style: {},
      paneIndex: 0,
    })
    expect(chart.drawings.state().count).toBe(1)
    expect(saved('SBIN')?.drawings.map((d) => d.tool)).toEqual(['horizontal-line'])
    await chart.drawings.setTool('ray')
    expect(
      controller(chart)
        ?.drawings()
        .map((d) => d.tool)
    ).toEqual(['horizontal-line'])
  })

  it('asks for the words of a note placed, keeps them, and takes an empty note off', async () => {
    const view = mountChart()
    const asked: string[] = []
    ;(view.chart as unknown as { cb: { onDrawNote(id: string): void } }).cb.onDrawNote = (id) =>
      asked.push(id)
    await view.chart.load('SBIN', 'NSE', '5m')
    await view.chart.drawings.setTool(null)
    const note = (controller(view.chart) as DrawingController).add({
      tool: 'text',
      points: [{ time: bars()[30].time, price: 815 }],
      style: {},
      text: { value: '' },
      paneIndex: 0,
    })
    expect(asked).toEqual([note.id])
    view.chart.drawings.setNote(note.id, '  Day high  ')
    expect(view.chart.drawings.noteOf(note.id)).toBe('Day high')
    expect(saved('SBIN')?.drawings).toHaveLength(1)
    view.chart.drawings.setNote(note.id, '   ')
    expect(controller(view.chart)?.drawings()).toEqual([])
  })
})
