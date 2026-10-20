/**
 * The phone chart's host, with the real chart engine and the real live-bar
 * pipeline. Only canvas painting, the network (history, quotes, the
 * market-data socket) and the market calendar's source are replaced.
 */
import { type Bar, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartStateView } from '@/lib/trading/chartState'
import type { LiveStatus } from '@/lib/trading/liveStatus'

const mocks = vi.hoisted(() => ({
  calendar: null as unknown,
  previousClose: vi.fn(async (..._args: unknown[]) => null as number | null),
}))

vi.mock('@/lib/trading/sessionHours', () => ({
  applySessionHours: () => {},
  sessionCalendarFor: async () => mocks.calendar,
}))
vi.mock('@/lib/trading/previousClose', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/previousClose')>()),
  previousClose: mocks.previousClose,
}))

import { PhoneChart, type PhoneTick, QUOTE_FALLBACK_MS, WAKE_RECONNECT_MS } from './phoneChart'

/** UTC seconds for a time of day in India on Tuesday 20 October 2026, a trading day. */
const ist = (h: number, mi: number, s = 0) => Date.UTC(2026, 9, 20, h, mi, s) / 1000 - 19_800

const nse = () =>
  new SessionCalendar({
    timezone: 'Asia/Kolkata',
    sessions: ['0915-1530:23456'],
    exceptions: { '2026-10-21': [] },
  })

/** Five-minute bars from the open to the one forming at ten. */
const fiveMinuteBars = (): Bar[] =>
  Array.from({ length: 10 }, (_, i) => {
    const close = 100 + i
    return {
      time: ist(9, 15 + i * 5),
      open: close - 0.5,
      high: close + 1,
      low: close - 1,
      close,
      volume: 1000 + i,
    }
  })

class FakeSocket {
  static all: FakeSocket[] = []
  readyState = 0
  sent: Array<Record<string, unknown>> = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  constructor(readonly url: string) {
    FakeSocket.all.push(this)
  }
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    this.onclose?.()
  }
  /** The proxy accepts the connection and the key. */
  accept() {
    this.readyState = 1
    this.onopen?.()
    this.receive({ type: 'auth', status: 'success' })
  }
  /** The connection drops from the far end. */
  drop() {
    this.readyState = 3
    this.onclose?.()
  }
  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
  subscriptions() {
    return this.sent.filter((m) => m.action === 'subscribe')
  }
}

const latestSocket = () => FakeSocket.all[FakeSocket.all.length - 1]

type Reply = { status?: string; data?: unknown; message?: string }
let historyReply: (body: Record<string, unknown>) => Reply | 'fail'
let quoteReply: (body: Record<string, unknown>) => Reply
const calls: Array<{ path: string; body: Record<string, unknown> }> = []

function respond(reply: Reply | 'fail') {
  if (reply === 'fail') return { ok: false, status: 500, json: async () => ({}) }
  return { ok: true, status: 200, json: async () => reply }
}

let visibility: 'visible' | 'hidden' = 'visible'
function setVisibility(state: 'visible' | 'hidden') {
  visibility = state
  document.dispatchEvent(new Event('visibilitychange'))
}

interface Heard {
  ticks: PhoneTick[]
  prevClose: number[]
  statuses: LiveStatus[]
  loads: ChartStateView[]
}

const charts: PhoneChart[] = []

function mount(): { chart: PhoneChart; heard: Heard; container: HTMLDivElement } {
  const heard: Heard = { ticks: [], prevClose: [], statuses: [], loads: [] }
  const container = document.createElement('div')
  Object.defineProperty(container, 'clientWidth', { configurable: true, value: 390 })
  Object.defineProperty(container, 'clientHeight', { configurable: true, value: 520 })
  document.body.appendChild(container)
  const chart = new PhoneChart({
    container,
    apiKey: 'test-key',
    wsUrl: 'ws://test.invalid',
    appMode: 'live',
    callbacks: {
      onTick: (tick) => heard.ticks.push(tick),
      onPrevClose: (value) => heard.prevClose.push(value),
      onStatus: (status) => heard.statuses.push(status),
      onLoad: (state) => heard.loads.push(state),
    },
  })
  charts.push(chart)
  return { chart, heard, container }
}

const internals = (chart: PhoneChart) =>
  chart as unknown as {
    bars: Bar[]
    volume: unknown
    interval: string
    chart: { axisChromeOptions(): { barCountdown?: boolean } }
  }
const countdown = (chart: PhoneChart) => internals(chart).chart.axisChromeOptions().barCountdown

const lastLine = (heard: Heard) => heard.statuses.at(-1)?.line

/** A depth frame as the proxy forwards it, with the day's volume beside the book. */
const depthFrame = (
  symbol: string,
  exchange: string,
  ltp: number,
  at: number,
  volume?: number,
  /** The exchange's time of the last trade, as the complete feed sends it (UTC seconds). */
  tradeAt?: number
) => ({
  type: 'market_data',
  symbol,
  exchange,
  mode: 3,
  data: {
    ltp,
    timestamp: Math.round(at * 1000),
    depth: { buy: [{ price: ltp - 0.05, quantity: 65 }], sell: [{ price: ltp, quantity: 65 }] },
    ...(volume === undefined ? {} : { volume }),
    ...(tradeAt === undefined ? {} : { last_trade_time: Math.round(tradeAt * 1000) }),
  },
})

beforeEach(() => {
  vi.useFakeTimers({ now: ist(10, 0, 30) * 1000 })
  FakeSocket.all = []
  calls.length = 0
  visibility = 'visible'
  mocks.calendar = nse()
  mocks.previousClose.mockReset()
  mocks.previousClose.mockResolvedValue(null)
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibility,
  })
  historyReply = () => ({
    status: 'success',
    data: fiveMinuteBars().map((bar) => ({ ...bar, timestamp: bar.time })),
  })
  quoteReply = () => ({ status: 'success', data: { ltp: 109.4, prev_close: 98.5, volume: 12_000 } })
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: { body?: string }) => {
      const body = JSON.parse(init?.body ?? '{}') as Record<string, unknown>
      const path = String(url).replace(/^.*\/api\/v1\//, '')
      calls.push({ path, body })
      if (path === 'history') return respond(historyReply(body))
      if (path === 'quotes') return respond(quoteReply(body))
      return respond({ status: 'error', message: 'not here' })
    })
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
  for (const chart of charts.splice(0)) chart.destroy()
  document.body.innerHTML = ''
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document, 'visibilityState')
})

describe('opening an instrument', () => {
  it('draws history, shows its last price, and subscribes to the book', async () => {
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    expect(heard.loads[0]).toMatchObject({ kind: 'loading', symbol: 'SBIN', interval: '5m' })
    expect(heard.loads.at(-1)).toMatchObject({ kind: 'ready' })
    expect(internals(chart).bars).toHaveLength(10)
    expect(internals(chart).volume).not.toBeNull()
    // The last close stands in until the first tick, marked as not from the stream.
    expect(heard.ticks[0]).toMatchObject({ ltp: 109, polled: true })
    const history = calls.find((call) => call.path === 'history')
    expect(history?.body).toMatchObject({ symbol: 'SBIN', exchange: 'NSE', interval: '5m' })

    latestSocket().accept()
    expect(latestSocket().subscriptions()).toEqual([
      expect.objectContaining({ symbol: 'SBIN', exchange: 'NSE', mode: 3 }),
    ])
  })

  it('subscribes an index to its last price only, with no volume pane', async () => {
    const { chart } = mount()
    await chart.load('NIFTY', 'NSE_INDEX', '1D')
    expect(internals(chart).interval).toBe('D')
    expect(internals(chart).volume).toBeNull()
    latestSocket().accept()
    expect(latestSocket().subscriptions()).toEqual([
      expect.objectContaining({ symbol: 'NIFTY', exchange: 'NSE_INDEX', mode: 1 }),
    ])
  })

  it("takes the previous close from the quote when it is the previous session's", async () => {
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    expect(heard.prevClose).toEqual([98.5])
    expect(mocks.previousClose).not.toHaveBeenCalled()
    // The quote's price is newer than the last bar's close, so the header takes it.
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 109.4, polled: true })
  })

  it("asks daily history when the quote's previous close is today's price", async () => {
    quoteReply = () => ({ status: 'success', data: { ltp: 109.4, prev_close: 109.4 } })
    mocks.previousClose.mockResolvedValue(97.25)
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    expect(mocks.previousClose).toHaveBeenCalledWith('test-key', 'SBIN', 'NSE', true)
    expect(heard.prevClose).toEqual([97.25])
  })

  it('says why history did not load, in words, and keeps the price streaming', async () => {
    historyReply = () => 'fail'
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    const failed = heard.loads.at(-1)
    expect(failed).toMatchObject({ kind: 'error', symbol: 'SBIN', interval: '5m' })
    expect(failed?.message).not.toMatch(/\d{3}|history request|api/i)
    latestSocket().accept()
    expect(latestSocket().subscriptions()).toHaveLength(1)
  })

  it('shows the first candle a tick forms when history had none', async () => {
    historyReply = () => ({ status: 'success', data: [] })
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    expect(heard.loads.at(-1)).toMatchObject({ kind: 'empty' })
    latestSocket().accept()
    latestSocket().receive(depthFrame('SBIN', 'NSE', 101, ist(10, 0, 31)))
    expect(heard.loads.at(-1)).toMatchObject({ kind: 'ready' })
    expect(internals(chart).bars.at(-1)).toMatchObject({ time: ist(10, 0), close: 101 })
  })
})

describe('real-time', () => {
  it('moves the forming candle and its volume on every tick, and says Real-time', async () => {
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    const socket = latestSocket()
    socket.accept()
    socket.receive(depthFrame('SBIN', 'NSE', 110.5, ist(10, 0, 31), 50_000))
    socket.receive(depthFrame('SBIN', 'NSE', 111, ist(10, 0, 32), 50_400))
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 111, polled: false, time: ist(10, 0, 32) })
    expect(lastLine(heard)).toEqual({ kind: 'live' })
    const forming = internals(chart).bars.at(-1)
    expect(forming).toMatchObject({ time: ist(10, 0), close: 111, high: 111 })
    // The day's count moved by 400 between the two frames.
    expect(forming?.volume).toBe(1009 + 400)
  })

  it('opens the next candle on the clock, flat at the last price, with no tick', async () => {
    vi.setSystemTime(ist(10, 4, 50) * 1000)
    const { chart } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    latestSocket().accept()
    latestSocket().receive(depthFrame('SBIN', 'NSE', 110.5, ist(10, 4, 51)))
    await vi.advanceTimersByTimeAsync(9_000)
    expect(internals(chart).bars.at(-1)?.time).toBe(ist(10, 0))
    await vi.advanceTimersByTimeAsync(1_200)
    expect(internals(chart).bars.at(-1)).toMatchObject({
      time: ist(10, 5),
      open: 110.5,
      high: 110.5,
      low: 110.5,
      close: 110.5,
      volume: 0,
    })
  })

  it('gives a late trade to the bar it belongs to, and the first trade after the boundary opens the next', async () => {
    vi.setSystemTime(ist(10, 4, 50) * 1000)
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    const socket = latestSocket()
    socket.accept()
    socket.receive(depthFrame('SBIN', 'NSE', 110.5, ist(10, 4, 51), 50_000, ist(10, 4, 51)))
    await vi.advanceTimersByTimeAsync(10_200) // 10:05:00.2: the clock has opened 10:05
    expect(internals(chart).bars.at(-1)).toMatchObject({ time: ist(10, 5), close: 110.5 })
    // The last trade of 10:00 to 10:05, at 10:04:59.7, arrives after the boundary.
    socket.receive(depthFrame('SBIN', 'NSE', 110.8, ist(10, 5, 0.4), 50_300, ist(10, 4, 59.7)))
    // The book moves again with no trade: still the same last trade.
    socket.receive(depthFrame('SBIN', 'NSE', 110.8, ist(10, 5, 0.6), 50_300, ist(10, 4, 59.7)))
    const [closed, forming] = internals(chart).bars.slice(-2)
    expect(closed).toMatchObject({ time: ist(10, 0), close: 110.8, volume: 1_009 + 300 })
    expect(forming).toEqual({
      time: ist(10, 5),
      open: 110.8,
      high: 110.8,
      low: 110.8,
      close: 110.8,
      volume: 0,
    })
    // The first trade of 10:05 is its open; one bar for the bucket, where it belongs.
    socket.receive(depthFrame('SBIN', 'NSE', 111, ist(10, 5, 1.1), 50_450, ist(10, 5, 1)))
    const bars = internals(chart).bars
    expect(bars.at(-1)).toMatchObject({ time: ist(10, 5), open: 111, close: 111, volume: 150 })
    expect(bars.filter((b) => b.time === ist(10, 5))).toHaveLength(1)
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 111, tradeTime: ist(10, 5, 1) })
  })

  it('says Reconnecting when the stream drops, then Delayed with quotes, never Real-time', async () => {
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    latestSocket().accept()
    latestSocket().receive(depthFrame('SBIN', 'NSE', 110.5, ist(10, 0, 31)))
    expect(lastLine(heard)).toEqual({ kind: 'live' })

    const dropped = heard.statuses.length
    latestSocket().drop()
    expect(lastLine(heard)).toMatchObject({ kind: 'connecting', again: true })
    await vi.advanceTimersByTimeAsync(QUOTE_FALLBACK_MS)
    expect(heard.ticks.at(-1)).toMatchObject({ ltp: 109.4, polled: true })
    expect(lastLine(heard)).toMatchObject({ kind: 'polling', stale: false })
    // A quote fills the gap; it is never called real-time.
    expect(heard.statuses.slice(dropped).map((s) => s.line.kind)).not.toContain('live')
  })

  it('reconnects at once when the phone wakes after more than a few seconds', async () => {
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    const first = latestSocket()
    first.accept()
    first.receive(depthFrame('SBIN', 'NSE', 110.5, ist(10, 0, 31)))

    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(WAKE_RECONNECT_MS + 1000)
    setVisibility('visible')
    expect(lastLine(heard)).toMatchObject({ kind: 'connecting', again: true })
    const second = latestSocket()
    expect(second).not.toBe(first)
    expect(first.readyState).toBe(3)
    second.accept()
    expect(second.subscriptions()).toEqual([
      expect.objectContaining({ symbol: 'SBIN', exchange: 'NSE', mode: 3 }),
    ])
    second.receive(depthFrame('SBIN', 'NSE', 110.75, ist(10, 0, 38)))
    expect(lastLine(heard)).toEqual({ kind: 'live' })
  })

  it('keeps the stream it has after a short glance away', async () => {
    const { chart } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    latestSocket().accept()
    const before = FakeSocket.all.length
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(2000)
    setVisibility('visible')
    expect(FakeSocket.all).toHaveLength(before)
  })

  it('says Market closed outside the session', async () => {
    vi.setSystemTime(ist(16, 0) * 1000)
    const { chart, heard } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    latestSocket().accept()
    expect(lastLine(heard)).toEqual({ kind: 'closed' })
    expect(heard.statuses.at(-1)?.opensAt).toBe(ist(9, 15) + 2 * 86_400)
  })
})

describe('the countdown to the bar close', () => {
  it('runs while the session is open, and moves each second without a tick', async () => {
    const { chart } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    latestSocket().accept()
    expect(countdown(chart)).toBe(true)
    const repaint = vi.spyOn(
      internals(chart).chart as never as { invalidate(): void },
      'invalidate'
    )
    await vi.advanceTimersByTimeAsync(3_000)
    expect(repaint.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it('is not shown in a closed market, where no bar is coming', async () => {
    vi.setSystemTime(ist(16, 0) * 1000)
    const { chart } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    expect(countdown(chart)).toBe(false)
  })

  it('is not shown for a day bar, which closes with the session and not at midnight', async () => {
    const { chart } = mount()
    await chart.load('NIFTY', 'NSE_INDEX', '1D')
    await vi.advanceTimersByTimeAsync(0)
    latestSocket().accept()
    expect(countdown(chart)).toBe(false)
  })
})

describe('the rest of its life', () => {
  it('redraws in the other mode with the same candles', async () => {
    const { chart } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    const bars = [...internals(chart).bars]
    chart.setAppMode('analyzer')
    expect(internals(chart).bars).toEqual(bars)
  })

  it('leaves nothing running once closed', async () => {
    const { chart } = mount()
    await chart.load('SBIN', 'NSE', '5m')
    await vi.advanceTimersByTimeAsync(0)
    latestSocket().accept()
    latestSocket().drop()
    chart.destroy()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(vi.getTimerCount()).toBe(0)
  })
})
