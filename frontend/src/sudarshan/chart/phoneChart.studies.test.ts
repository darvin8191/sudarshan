/**
 * The indicator set on the phone chart (M4.2, C3), on the real chart engine
 * and its real indicator tier: applied to every chart the host builds, kept
 * through a rebuild and a new instrument, untouched by a wake reconnect, the
 * engine's own legend rows hidden for the page's, and a study that cannot run
 * on an instrument skipped with a line.
 */
import { type Bar, type Chart, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PROBE_TIMEOUT_MS } from '@/lib/trading/liveTicks'
import { ist } from '@/test/tapes/scenarios'
import { depthFrame, FakeProxySocket } from '@/test/tickTape'
import type { PhoneChart } from './phoneChart'
import { VOLUME_MA } from './phoneStudies'
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
const scripts = vi.hoisted(() => ({
  order: [] as string[],
  ensure: vi.fn(async (_ids: string[]) => ({ loaded: [], errors: [] })),
}))
// The owner's own files, from the server in the app: here, the order they are loaded in.
vi.mock('@/lib/trading/customIndicators', () => ({
  ensureCustomIndicators: async (ids: string[]) => {
    scripts.order.push('custom')
    const { registerIndicator } = await import('openalgo-charts')
    for (const id of ids.filter((one) => one.startsWith('my-')))
      registerIndicator({
        id,
        name: 'My Band',
        category: 'Custom',
        placement: 'onchart',
        inputs: [{ key: 'width', type: 'number', label: 'Width', default: 2 }],
        plots: [{ key: 'mid', type: 'line', title: 'Mid', style: { color: '#123456' } }],
        calc: (bars: Array<{ close: number }>) => ({ mid: bars.map((bar) => bar.close) }),
      } as never)
    return scripts.ensure(ids)
  },
  loadCustomIndicators: async () => {
    scripts.order.push('custom')
    return { loaded: [], errors: [{ file: 'broken.js', message: 'it does not register anything' }] }
  },
}))
vi.mock('@/lib/trading/openscriptStudies', () => ({
  loadOpenScriptStudies: async () => {
    scripts.order.push('openscript')
    return { loaded: [], errors: [] }
  },
}))
vi.mock('@/lib/trading/previousClose', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/previousClose')>()),
  previousClose: async () => null,
}))

/** Sixty five-minute candles from the open of Monday 19 October, then today's to 10:00. */
const bars = (): Bar[] => {
  const out: Bar[] = []
  let close = 800
  for (let i = 0; i < 75; i++) {
    close += i % 4 === 0 ? -1.5 : 1
    out.push({
      time: ist(9, 15) / 1000 - 86_400 + i * 300,
      open: close - 0.5,
      high: close + 1,
      low: close - 1,
      close,
      volume: 1_000 + i * 10,
    })
  }
  return out
}

const engine = (chart: PhoneChart) => (chart as unknown as { chart: Chart }).chart

beforeEach(() => {
  installHost(ist(10, 0, 30))
  host.replies.history = () => historyOf(bars())
})
afterEach(() => removeHost())

async function opened(symbol = 'SBIN', exchange = 'NSE') {
  const view = mountChart()
  await view.chart.setStudies([
    { id: 'ema', settings: { length: 21 } },
    { id: 'rsi', settings: {} },
  ])
  await view.chart.load(symbol, exchange, '5m')
  await vi.advanceTimersByTimeAsync(0)
  return view
}

describe('the indicator set on the chart', () => {
  it('adds each study in order, an overlay on the candles and a pane for RSI', async () => {
    const { chart, heard } = await opened()
    const studies = engine(chart).indicators()
    expect(studies.map((s) => s.indicatorId)).toEqual(['ema', 'rsi'])
    expect(studies[0].settings().length).toBe(21)
    expect(studies[0].paneIndex).toBe(0)
    // Price, volume, then RSI's own pane.
    expect(studies[1].paneIndex).toBe(2)
    expect(heard.ticks.length).toBeGreaterThan(0)
  })

  it("hides the engine's own legend rows and their small buttons, and gives the page a line per study", async () => {
    const { chart } = await opened()
    for (const study of engine(chart).indicators())
      expect(study.legend()?.options().visible).toBe(false)
    const rows = chart.legendRows()
    expect(rows.map((row) => [row.title, row.pane])).toEqual([
      ['EMA', 0],
      ['RSI', 2],
    ])
    expect(rows[0].params).toContain('21')
    expect(rows[0].values[0]).toMatch(/^[\d,]+\.\d{2}$/)
    expect(Number(rows[1].values[0])).toBeGreaterThan(0)
    expect(rows[1].top).toBeGreaterThan(rows[0].top)
  })

  it('reads the values at a held candle, and at the last bar otherwise', async () => {
    const { chart } = await opened()
    const last = chart.legendRows()[0].values[0]
    const held = chart.legendRows(bars()[40].time)[0].values[0]
    expect(held).not.toBe(last)
    const ema =
      engine(chart).indicators()[0].values().ema ??
      Object.values(engine(chart).indicators()[0].values())[0]
    expect(held).toBe(
      (ema[40] as number).toLocaleString('en-IN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    )
  })

  it('keeps the set through a rebuild in another style and on another instrument', async () => {
    const { chart } = await opened()
    await chart.setStyle('line')
    expect(
      engine(chart)
        .indicators()
        .map((s) => s.indicatorId)
    ).toEqual(['ema', 'rsi'])
    await chart.load('INFY', 'NSE', '5m')
    expect(
      engine(chart)
        .indicators()
        .map((s) => s.indicatorId)
    ).toEqual(['ema', 'rsi'])
  })

  it('leaves the studies untouched across a wake reconnect, which builds nothing anew', async () => {
    const { chart } = await opened()
    const socket = FakeProxySocket.latest() as FakeProxySocket
    socket.accept()
    const before = engine(chart).indicators()
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(10_000)
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(PROBE_TIMEOUT_MS)
    const replaced = FakeProxySocket.latest() as FakeProxySocket
    expect(replaced).not.toBe(socket)
    replaced.accept()
    replaced.receive(depthFrame('SBIN', 'NSE', { ltp: 880, at: Date.now(), tradeAt: Date.now() }))
    const after = engine(chart).indicators()
    expect(after.map((s) => s.id)).toEqual(before.map((s) => s.id))
  })

  it('skips open interest on a share with a line, keeping it in the set', async () => {
    const view = mountChart()
    await view.chart.setStudies([{ id: 'open-interest', settings: {} }])
    await view.chart.load('SBIN', 'NSE', '5m')
    expect(engine(view.chart).indicators()).toEqual([])
    expect(view.chart.legendRows()).toEqual([
      expect.objectContaining({
        note: 'Open Interest is not shown: this instrument has no open interest.',
      }),
    ])
  })

  it('draws the volume average on the volume pane it already has, and skips it on an index', async () => {
    const view = mountChart()
    await view.chart.setStudies([{ id: VOLUME_MA, settings: { length: 3 } }])
    await view.chart.load('SBIN', 'NSE', '5m')
    expect(view.chart.legendRows()[0]).toMatchObject({ title: 'Vol MA', params: '3', pane: 1 })
    const last = bars().slice(-3)
    const average = last.reduce((total, b) => total + (b.volume ?? 0), 0) / 3
    expect(view.chart.legendRows()[0].values[0]).toBe(
      new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 2 }).format(
        average
      )
    )
    await view.chart.load('NIFTY', 'NSE_INDEX', '5m')
    expect(view.chart.legendRows()[0].note).toBe(
      'Volume moving average is not shown: an index has no volume.'
    )
  })

  it('takes a set off the chart when it is emptied', async () => {
    const { chart } = await opened()
    await chart.setStudies([])
    expect(engine(chart).indicators()).toEqual([])
    expect(chart.legendRows()).toEqual([])
  })

  it("writes the price axis and an overlay's values in the instrument's tick", async () => {
    const { chart } = await opened()
    chart.setTick(0.05)
    expect(engine(chart).priceScaleOptions().minMove).toBe(0.05)
    await chart.setStyle('bar')
    expect(engine(chart).priceScaleOptions().minMove).toBe(0.05)
  })

  it("lists every built-in by name, and marks the owner's own", async () => {
    const view = mountChart()
    await view.chart.loadIndicatorTier()
    const builtIns = view.chart.catalogue().filter((study) => !study.mine)
    expect(builtIns).toHaveLength(112)
    expect(builtIns.map((s) => s.name)).toEqual(
      [...builtIns.map((s) => s.name)].sort((a, b) => a.localeCompare(b))
    )
    await view.chart.setStudies([{ id: 'my-band', settings: {} }])
    expect(view.chart.catalogue().find((study) => study.id === 'my-band')).toMatchObject({
      name: 'My Band',
      mine: true,
    })
  })

  it("loads the owner's JavaScript indicators before his OpenScript studies, and says what failed", async () => {
    scripts.order.length = 0
    const view = mountChart()
    const problems = await view.chart.loadMyScripts()
    expect(scripts.order).toEqual(['custom', 'openscript'])
    expect(problems).toEqual(['broken.js: it does not register anything'])
  })

  it('restores a saved set naming one of his own from just the file it comes from', async () => {
    scripts.ensure.mockClear()
    const view = mountChart()
    await view.chart.setStudies([
      { id: 'ema', settings: {} },
      { id: 'my-restored', settings: { width: 3 } },
    ])
    await view.chart.load('SBIN', 'NSE', '5m')
    expect(scripts.ensure).toHaveBeenCalledWith(['my-restored'])
    expect(
      engine(view.chart)
        .indicators()
        .map((s) => s.indicatorId)
    ).toEqual(['ema', 'my-restored'])
  })

  it("gives the settings form from the engine's descriptor, its values over the defaults", async () => {
    const { chart } = await opened()
    const form = chart.studyForm(0)
    expect(form?.name).toBe('EMA')
    expect(form?.fields.map((f) => [f.key, f.type])).toEqual([
      ['length', 'number'],
      ['source', 'select'],
      ['timeframe', 'interval'],
    ])
    expect(form?.values).toMatchObject({ length: 21, source: 'close', timeframe: '' })
    expect(chart.studyForm(9)).toBeNull()
  })

  it('changes a setting where the study stands, without adding it again', async () => {
    const { chart } = await opened()
    const before = engine(chart)
      .indicators()
      .map((s) => s.id)
    await chart.setStudies([
      { id: 'ema', settings: { length: 50 } },
      { id: 'rsi', settings: {} },
    ])
    const after = engine(chart).indicators()
    expect(after.map((s) => s.id)).toEqual(before)
    expect(after[0].settings().length).toBe(50)
    // Back to defaults: a key left out of the set returns to the descriptor's default.
    await chart.setStudies([
      { id: 'ema', settings: {} },
      { id: 'rsi', settings: {} },
    ])
    expect(engine(chart).indicators()[0].settings().length).toBe(9)
  })
})
