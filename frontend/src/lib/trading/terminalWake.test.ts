/**
 * SUDARSHAN M4.1 (SCR-F): the terminal's candle clock across a sleep, played
 * from a committed tick tape into the terminal's own socket, live-bar builder
 * and clock. Only canvas painting, the network and the page's visibility are
 * replaced.
 *
 * A phone or a laptop that sleeps stops the clock its timers run on, so a
 * timer set before the sleep still has the rest of its wait to run after it.
 * The candle clock's timer is one of those: left alone, the next candle
 * nobody trades opens up to a bar late.
 */
import { type Bar, createChart, type SeriesApi, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import sleepBoundary from '@/test/tapes/sleep-boundary-1m.json'
import { FakeProxySocket, socketSink, type Tape, TapePlayer } from '@/test/tickTape'

vi.mock('./sessionHours', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./sessionHours')>()),
  sessionCalendarFor: async () =>
    new SessionCalendar({ timezone: 'Asia/Kolkata', sessions: ['0915-1530:23456'] }),
}))

import { resetInstrumentSessions } from './instrumentSession'
import { type SymbolView, TradingTerminal } from './terminal'

/** The history owner as the clock and the visibility handler use it. */
interface History {
  pushBar(bar: Bar, options?: unknown): void
  refresh(): Promise<readonly Bar[]>
  bars(): readonly Bar[]
  setVisible(visible: boolean): void
  getState(): { request: null }
  destroy(): void
}

type TerminalState = {
  chart: ReturnType<typeof createChart>
  price: SeriesApi
  volume: SeriesApi
  rawBars: Bar[]
  sym: SymbolView
  interval: string
  data: History | null
  alerts: { list(): Array<{ state: string }>; destroy(): void } | null
  setPriceData(): void
  connectLive(): void
}

const sec = (ms: number) => Math.floor(ms / 1000)
/** Epoch milliseconds for a time of day in India on the tapes' day, Tuesday 20 October 2026. */
const ist = (h: number, m: number, s = 0) =>
  Date.UTC(2026, 9, 20, h, m, 0) + Math.round(s * 1000) - 19_800_000

let visibility: 'visible' | 'hidden' = 'visible'
function setVisibility(state: 'visible' | 'hidden'): void {
  visibility = state
  document.dispatchEvent(new Event('visibilitychange'))
}

const terminals: TradingTerminal[] = []
/** Every POST the terminal made to /api/v1, by path. */
const asked: string[] = []

beforeEach(() => {
  vi.useFakeTimers({ now: ist(10, 0, 0) })
  localStorage.clear()
  FakeProxySocket.all = []
  asked.length = 0
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibility,
  })
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
  Reflect.deleteProperty(document, 'visibilityState')
})

/**
 * A terminal initialised as the page does it, on SBIN 1m with history up to
 * the candle forming at `nowMs`, its socket accepted and its live bar started.
 */
async function openTerminal(nowMs: number) {
  vi.setSystemTime(nowMs)
  const container = document.createElement('div')
  const terminal = new TradingTerminal({
    apiKey: 'test',
    wsUrl: 'ws://test.invalid',
    container,
    legendEl: document.createElement('div'),
    getTheme: () => ({ mode: 'light', appMode: 'live' }),
    callbacks: {
      onReady() {},
      onToast() {},
      onWsState() {},
      onSymbolLoaded() {},
      onLtp() {},
    },
  })
  terminals.push(terminal)
  const state = terminal as unknown as TerminalState
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
    asked.push(path)
    if (path === 'quotes') throw new Error('no quote')
    return { data: { minutes: ['1m'] } } as never
  })
  vi.spyOn(terminal, 'search').mockResolvedValue([])
  await terminal.init()
  state.sym = {
    symbol: 'SBIN',
    exchange: 'NSE',
    name: 'State Bank of India',
    lotsize: 1,
    lots: false,
    tick: 0.05,
    freezeQty: 0,
    quoteOnly: false,
    productOptions: ['MIS', 'CNC'],
    product: 'MIS',
  } as SymbolView
  state.interval = '1m'
  const forming = sec(nowMs) - (sec(nowMs) % 60)
  state.rawBars = [forming - 120, forming - 60, forming].map((time) => ({
    time,
    open: 120.25,
    high: 121,
    low: 120,
    close: 120.5,
    volume: 1_000,
  }))
  state.setPriceData()
  // History's owner, replaced where it meets the network.
  const refresh = vi.fn(async () => state.rawBars)
  state.data?.destroy()
  state.data = {
    pushBar() {},
    refresh,
    // History holds nothing the clock opened: only what was traded.
    bars: () => state.rawBars.filter((bar) => bar.volume > 0),
    setVisible() {},
    // No request: a snapshot of it belongs to no pane and is ignored.
    getState: () => ({ request: null }),
    destroy() {},
  }
  state.connectLive()
  await vi.advanceTimersByTimeAsync(0)
  const socket = FakeProxySocket.latest() as FakeProxySocket
  // The proxy is alive throughout: it answers every ping, the wake's included.
  const send = socket.send.bind(socket)
  socket.send = (data: string) => {
    send(data)
    if (JSON.parse(data).action === 'ping') queueMicrotask(() => socket.pong())
  }
  socket.accept()
  await vi.advanceTimersByTimeAsync(0)
  return { terminal, state, socket, refresh }
}

/** Plays a tape into the terminal's socket, with a sleep as the device has it. */
function player(tape: Tape): TapePlayer {
  return new TapePlayer(
    tape,
    {
      frame: socketSink(),
      page: setVisibility,
      // The wall clock jumps; every timer keeps its remaining wait.
      sleep: (ms) => {
        vi.setSystemTime(Date.now() + ms)
      },
    },
    (ms) => vi.advanceTimersByTimeAsync(ms)
  )
}

describe('the terminal after a sleep', () => {
  it('opens the next candle on time, though a timer set before the sleep was still waiting', async () => {
    const tape = sleepBoundary as Tape
    const { state } = await openTerminal(tape.start)
    const play = player(tape)
    // Awake again at 10:13:30, and a trade a second later.
    await play.to(ist(10, 13, 59.9) - tape.start)
    expect(state.rawBars.at(-1)).toMatchObject({ time: sec(ist(10, 13)), close: 120.6 })
    // 10:14:00 passes with nobody trading. The timer set before the sleep
    // still had 58 s to run at the wake, and would open this candle at 10:14:28.
    await play.to(ist(10, 14, 0.25) - tape.start)
    expect(state.rawBars.at(-1)).toEqual({
      time: sec(ist(10, 14)),
      open: 120.6,
      high: 120.6,
      low: 120.6,
      close: 120.6,
      volume: 0,
    })
    expect(state.price.getData().at(-1)?.time).toBe(sec(ist(10, 14)))
    // The first trade of 10:14 replaces the flat candle: it is the open.
    await play.end()
    expect(state.rawBars.at(-1)).toMatchObject({ time: sec(ist(10, 14)), open: 120.7 })
    const times = state.rawBars.map((bar) => bar.time)
    expect(new Set(times).size).toBe(times.length)
    // The socket it had before the sleep answered, and was kept.
    expect(FakeProxySocket.all).toHaveLength(1)
  })

  it('reads no history for the clock while the page is hidden, and none is missed on the wake', async () => {
    const { state, refresh } = await openTerminal(ist(10, 0, 30))
    refresh.mockClear()
    setVisibility('hidden')
    // Ten minutes hidden with timers running (another tab, not a sleep): the
    // clock opens a flat candle each minute, and each asked history before.
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(state.rawBars.at(-1)).toMatchObject({ time: sec(ist(10, 10)), volume: 0 })
    expect(refresh).not.toHaveBeenCalled()
    // Shown again, history is read again.
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(63_000)
    expect(refresh).toHaveBeenCalled()
  })

  it('goes on reading history while hidden when an alert is waiting on the candle', async () => {
    const { state, refresh } = await openTerminal(ist(10, 0, 30))
    state.alerts = { list: () => [{ state: 'armed' }], destroy() {} }
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(63_000)
    expect(refresh).toHaveBeenCalled()
  })
})
