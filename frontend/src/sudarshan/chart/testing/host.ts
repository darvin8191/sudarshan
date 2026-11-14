/**
 * The phone chart's host under test: the real chart engine and the real
 * live-bar pipeline, with only canvas painting, the network (history, quotes,
 * the instrument's facts, the market-data socket) and the page's visibility
 * replaced. The socket is the tick tape's `FakeProxySocket`.
 *
 * A test file still declares its own module mocks for the market calendar and
 * the previous close (`vi.mock` is hoisted per file); everything else is here:
 * `installHost` in `beforeEach`, `removeHost` in `afterEach`, `mountChart` per
 * test.
 */
import type { Bar, MarketDepth } from 'openalgo-charts'
import { vi } from 'vitest'
import type { ChartStateView } from '@/lib/trading/chartState'
import { resetInstrumentSessions } from '@/lib/trading/instrumentSession'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import { FakeProxySocket } from '@/test/tickTape'
import { PhoneChart, type PhoneTick } from '../phoneChart'
import type { ChartStyle } from '../prefs'

export type Reply = { status?: string; data?: unknown; message?: string }

export interface HostReplies {
  /** POST /api/v1/history, by its body. */
  history(body: Record<string, unknown>): Reply | 'fail'
  /** POST /api/v1/quotes. */
  quote(body: Record<string, unknown>): Reply
  /** GET /sudarshan/session/instrument: no stop and no closing window unless a test says. */
  facts(query: URLSearchParams): Reply
}

/** History's answer for a list of bars. */
export const historyOf = (bars: readonly Bar[]): Reply => ({
  status: 'success',
  data: bars.map((bar) => ({ ...bar, timestamp: bar.time })),
})

export const host = {
  replies: null as unknown as HostReplies,
  /** Every POST to /api/v1, in order. */
  calls: [] as Array<{ path: string; body: Record<string, unknown> }>,
  visibility: 'visible' as 'visible' | 'hidden',
  charts: [] as PhoneChart[],
}

export function setVisibility(state: 'visible' | 'hidden'): void {
  host.visibility = state
  document.dispatchEvent(new Event('visibilitychange'))
}

function respond(reply: Reply | 'fail') {
  if (reply === 'fail') return { ok: false, status: 500, json: async () => ({}) }
  return { ok: true, status: 200, json: async () => reply }
}

/** Fake timers from `now` (epoch ms), the network and the canvas replaced. */
export function installHost(now: number): void {
  vi.useFakeTimers({ now })
  FakeProxySocket.all = []
  host.calls.length = 0
  host.charts.length = 0
  host.visibility = 'visible'
  host.replies = {
    history: () => ({ status: 'success', data: [] }),
    quote: () => ({ status: 'error', message: 'no quote' }),
    facts: () => ({ status: 'success', data: { day: '', stops_at: null, closing: null } }),
  }
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => host.visibility,
  })
  resetInstrumentSessions()
  vi.stubGlobal('WebSocket', FakeProxySocket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: { body?: string }) => {
      if (String(url).startsWith('/sudarshan/session/instrument?'))
        return respond(host.replies.facts(new URL(String(url), 'http://test.invalid').searchParams))
      const body = JSON.parse(init?.body ?? '{}') as Record<string, unknown>
      const path = String(url).replace(/^.*\/api\/v1\//, '')
      host.calls.push({ path, body })
      if (path === 'history') return respond(host.replies.history(body))
      if (path === 'quotes') return respond(host.replies.quote(body))
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
}

export function removeHost(): void {
  for (const chart of host.charts.splice(0)) chart.destroy()
  document.body.innerHTML = ''
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document, 'visibilityState')
}

/** What the chart told its page. */
export interface Heard {
  ticks: PhoneTick[]
  prevClose: number[]
  statuses: LiveStatus[]
  loads: ChartStateView[]
  closes: Array<number | null>
  depths: MarketDepth[]
}

export function mountChart(options: { style?: ChartStyle } = {}): {
  chart: PhoneChart
  heard: Heard
  container: HTMLDivElement
} {
  const heard: Heard = {
    ticks: [],
    prevClose: [],
    statuses: [],
    loads: [],
    closes: [],
    depths: [],
  }
  const container = document.createElement('div')
  Object.defineProperty(container, 'clientWidth', { configurable: true, value: 360 })
  Object.defineProperty(container, 'clientHeight', { configurable: true, value: 520 })
  document.body.appendChild(container)
  const chart = new PhoneChart({
    container,
    apiKey: 'test-key',
    wsUrl: 'ws://test.invalid',
    appMode: 'live',
    style: options.style,
    callbacks: {
      onTick: (tick) => heard.ticks.push(tick),
      onPrevClose: (value) => heard.prevClose.push(value),
      onStatus: (status) => heard.statuses.push(status),
      onLoad: (state) => heard.loads.push(state),
      onSessionClose: (at) => heard.closes.push(at),
      onDepth: (depth) => heard.depths.push(depth),
    },
  })
  host.charts.push(chart)
  return { chart, heard, container }
}

/** The chart's candles as it holds them. */
export const barsOf = (chart: PhoneChart): Bar[] => (chart as unknown as { bars: Bar[] }).bars

/** The feed's line as last reported. */
export const lineOf = (heard: Heard) => heard.statuses.at(-1)?.line
