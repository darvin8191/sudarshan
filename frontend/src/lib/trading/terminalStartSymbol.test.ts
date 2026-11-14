/**
 * SUDARSHAN (SCR-P item 9): the instrument a pane opens on, through the terminal's own start-up.
 * A pane's own last instrument first, then the pane the grid was opened from (`seedSymbol`), and
 * the terminal's default only after both. Before, a new pane of a grid went straight to the
 * default, or stayed empty when that could not be read. Only painting, the network and the
 * instrument load itself are replaced.
 */
import { createChart, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeProxySocket } from '@/test/tickTape'

vi.mock('./sessionHours', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./sessionHours')>()),
  sessionCalendarFor: async () =>
    new SessionCalendar({ timezone: 'Asia/Kolkata', sessions: ['0915-1530:23456'] }),
}))

import { resetInstrumentSessions } from './instrumentSession'
import type { SymbolRef } from './startSymbol'
import { TradingTerminal } from './terminal'

const terminals: TradingTerminal[] = []

beforeEach(() => {
  vi.useFakeTimers({ now: Date.UTC(2026, 9, 20, 4, 30, 0) })
  localStorage.clear()
  FakeProxySocket.all = []
  resetInstrumentSessions()
  vi.stubGlobal('WebSocket', FakeProxySocket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ status: 'success', data: { day: '', stops_at: null, closing: null } }),
    }))
  )
  const context = new Proxy(
    {
      measureText: (text: string) => ({ width: text.length * 7 }),
      createLinearGradient: () => ({ addColorStop() {} }),
      getImageData: () => ({ data: new Uint8ClampedArray([0, 0, 0, 255]) }),
    },
    { get: (target, key) => target[key as keyof typeof target] ?? (() => {}) }
  )
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    context as unknown as CanvasRenderingContext2D
  )
})

afterEach(() => {
  for (const terminal of terminals.splice(0)) terminal.destroy()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

/**
 * A pane started as the page starts it. `listed` are the instruments the master has; the result
 * is what was looked up, in order, and what was loaded.
 */
async function start(options: {
  storageKey: string
  seedSymbol?: SymbolRef | null
  listed: SymbolRef[]
}) {
  const container = document.createElement('div')
  const terminal = new TradingTerminal({
    apiKey: 'test',
    wsUrl: 'ws://test.invalid',
    container,
    legendEl: document.createElement('div'),
    storageKey: options.storageKey,
    seedSymbol: options.seedSymbol,
    getTheme: () => ({ mode: 'light', appMode: 'live' }),
    callbacks: { onReady() {}, onToast() {}, onWsState() {}, onSymbolLoaded() {}, onLtp() {} },
  })
  terminals.push(terminal)
  const state = terminal as unknown as {
    chart: ReturnType<typeof createChart>
    price: unknown
    volume: unknown
    loadSymbol(row: SymbolRef): Promise<boolean>
  }
  state.chart = createChart(container, {
    shortcuts: false,
    timeNavigator: false,
    raf: {
      schedule: (cb) => {
        cb()
        return 1
      },
      cancel() {},
    },
  })
  state.chart.applySize(800, 600)
  state.price = state.chart.addSeries('candlestick')
  state.volume = state.chart.addSeries('histogram', { priceScaleId: '' })
  vi.spyOn(terminal, 'api').mockImplementation(async (path: string) => {
    if (path === 'quotes') throw new Error('no quote')
    return { data: { minutes: ['1m'] } } as never
  })
  const looked: string[] = []
  vi.spyOn(terminal, 'search').mockImplementation(async (symbol: string, exchange?: string) => {
    looked.push(`${exchange}:${symbol}`)
    return options.listed.filter(
      (row) => row.symbol === symbol && row.exchange === exchange
    ) as never
  })
  const loaded: string[] = []
  vi.spyOn(state, 'loadSymbol').mockImplementation(async (row: SymbolRef) => {
    loaded.push(`${row.exchange}:${row.symbol}`)
    return true
  })
  await terminal.init()
  return { looked, loaded }
}

const TESTCO = { symbol: 'TESTCO', exchange: 'NSE' }
const OLDCO = { symbol: 'OLDCO', exchange: 'NSE' }
const BHEL = { symbol: 'BHEL', exchange: 'NSE' }

describe('the instrument a pane opens on', () => {
  it('is the focused pane’s for a new pane of a grid, and the default is never asked for', async () => {
    const pane = await start({
      storageKey: 'oa-trading-p1',
      seedSymbol: TESTCO,
      listed: [TESTCO, BHEL],
    })
    expect(pane.loaded).toEqual(['NSE:TESTCO'])
    expect(pane.looked).toEqual(['NSE:TESTCO'])
  })

  it('is the pane’s own last instrument where it has one, whatever the focused pane shows', async () => {
    localStorage.setItem('oa-trading-p1-symbol', JSON.stringify(OLDCO))
    const pane = await start({
      storageKey: 'oa-trading-p1',
      seedSymbol: TESTCO,
      listed: [OLDCO, TESTCO, BHEL],
    })
    expect(pane.loaded).toEqual(['NSE:OLDCO'])
    expect(pane.looked).toEqual(['NSE:OLDCO'])
  })

  it('is the focused pane’s when the pane’s own is no longer in the master', async () => {
    localStorage.setItem('oa-trading-p1-symbol', JSON.stringify(OLDCO))
    const pane = await start({
      storageKey: 'oa-trading-p1',
      seedSymbol: TESTCO,
      listed: [TESTCO, BHEL],
    })
    expect(pane.loaded).toEqual(['NSE:TESTCO'])
  })

  it('is the default when the focused pane’s cannot be found either, or none was given', async () => {
    const lost = await start({ storageKey: 'oa-trading-p2', seedSymbol: TESTCO, listed: [BHEL] })
    expect(lost.looked).toEqual(['NSE:TESTCO', 'NSE:BHEL'])
    expect(lost.loaded).toEqual(['NSE:BHEL'])
    const alone = await start({ storageKey: 'oa-trading-p3', listed: [BHEL] })
    expect(alone.looked).toEqual(['NSE:BHEL'])
    expect(alone.loaded).toEqual(['NSE:BHEL'])
  })

  it('opens a computed chart, which has no exchange, without looking it up in the master', async () => {
    const pane = await start({
      storageKey: 'oa-trading-p1',
      seedSymbol: { symbol: 'AAA/BBB', exchange: '' },
      listed: [BHEL],
    })
    expect(pane.looked).toEqual([])
    expect(pane.loaded).toEqual([':AAA/BBB'])
  })

  it('looks a hyphenated name up as the instrument it is, never as a subtraction', async () => {
    const hyphen = { symbol: 'TEST-CO', exchange: 'NSE' }
    const pane = await start({
      storageKey: 'oa-trading-p1',
      seedSymbol: hyphen,
      listed: [hyphen, BHEL],
    })
    expect(pane.looked).toEqual(['NSE:TEST-CO'])
    expect(pane.loaded).toEqual(['NSE:TEST-CO'])
  })
})
