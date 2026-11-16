/**
 * A trade's marks on the real chart engine (M14.8): the host says when a load drew its
 * candles and which, places the view where it is asked, and draws a trade's marks as
 * tags in the side's colour; then the whole of it, from the journal's answer to the marks
 * and the view, and a trade older than the minute candles moving to daily ones.
 */

import { act, renderHook } from '@testing-library/react'
import { type Bar, type Chart, type SeriesApi, SessionCalendar } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ist } from '@/test/tapes/scenarios'
import {
  MARK_ROOM,
  type PhoneChart,
  PLAIN_ROOM,
  TAG_FONT_PX,
  tagText,
  tagWidth,
} from './phoneChart'
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
// The token asked for, as written: jsdom has no stylesheet to resolve it through.
vi.mock('@/lib/trading/chartTheme', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/chartTheme')>()),
  resolveCssColor: (colour: string) => colour,
}))
vi.mock('@/lib/trading/previousClose', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/trading/previousClose')>()),
  previousClose: async () => null,
}))
const call = vi.hoisted(() => vi.fn())
vi.mock('@/sudarshan/journal/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/journal/api')>()),
  journalCall: (...args: unknown[]) => call(...args),
}))

import { OLDER_THAN_HISTORY, SHOWN_DAILY, useTripMarkers } from './tripMarkers'

/** Minute candles of yesterday and today from 09:15 to 09:15 + `count` minutes each. */
function minutes(count = 300): Bar[] {
  const out: Bar[] = []
  for (const start of [ist(9, 15) - 86_400_000, ist(9, 15)])
    for (let i = 0; i < count; i++)
      out.push({
        time: start / 1000 + i * 60,
        open: 800 + i * 0.05,
        high: 801 + i * 0.05,
        low: 799 + i * 0.05,
        close: 800.5 + i * 0.05,
        volume: 1_000,
      })
  return out
}

/** Daily candles at midnight in India: the forty days up to today. */
function days(): Bar[] {
  return Array.from({ length: 40 }, (_, i) => ({
    time: (ist(0, 0) - (39 - i) * 86_400_000) / 1000,
    open: 780 + i,
    high: 790 + i,
    low: 770 + i,
    close: 785 + i,
    volume: 100_000,
  }))
}

type Layer = { _markers: Array<Record<string, unknown>> }
const layer = (chart: PhoneChart) => (chart as unknown as { markerLayer: Layer | null }).markerLayer
const engine = (chart: PhoneChart) => (chart as unknown as { chart: Chart }).chart

beforeEach(() => {
  installHost(ist(13, 0))
  host.replies.history = (body) => historyOf(body.interval === 'D' ? days() : minutes())
  call.mockReset()
})
afterEach(() => removeHost())

describe('the host a trade view uses', () => {
  it('says when a load drew its candles, with the interval, and gives their times', async () => {
    const { chart } = mountChart()
    const heard: Array<[string, number]> = []
    chart.onLoaded((interval) => heard.push([interval, chart.barTimes().length]))
    await chart.load('SBIN', 'NSE', '1m')
    await chart.load('SBIN', 'NSE', 'D')
    expect(heard).toEqual([
      ['1m', minutes().length],
      ['D', days().length],
    ])
    expect(chart.barTimes()).toEqual(days().map((bar) => bar.time))
  })

  it('says nothing for a load that failed or found no candles, and nothing once let go', async () => {
    const { chart } = mountChart()
    const heard = vi.fn()
    const stop = chart.onLoaded(heard)
    host.replies.history = () => 'fail'
    await chart.load('SBIN', 'NSE', '1m')
    host.replies.history = () => historyOf([])
    await chart.load('SBIN', 'NSE', '5m')
    expect(heard).not.toHaveBeenCalled()
    host.replies.history = () => historyOf(minutes())
    stop()
    await chart.load('SBIN', 'NSE', '1m')
    expect(heard).not.toHaveBeenCalled()
  })

  it('places the view where it is asked, and it stays there', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    chart.showBars({ from: 40, to: 100 })
    await vi.advanceTimersByTimeAsync(1_000)
    const range = engine(chart).getVisibleLogicalRange()
    expect(range?.from).toBeCloseTo(40, 0)
    expect(range?.to).toBeCloseTo(100, 0)
  })

  it('draws a trade mark as a tag in the side colour, under a buy and over a sell', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    const [a, b] = [chart.barTimes()[10], chart.barTimes()[20]]
    chart.setOverlays({
      markers: [
        { id: 'e', time: a, side: 'BUY', shape: 'tag', text: 'B 803.05', short: 'B' },
        { id: 'x', time: b, side: 'SELL', shape: 'tag', text: 'S', short: 'S' },
      ],
    })
    expect(layer(chart)?._markers).toEqual([
      expect.objectContaining({
        id: 'e',
        time: a,
        shape: 'labelUp',
        position: 'belowBar',
        fontSize: TAG_FONT_PX,
        color: 'var(--buy-solid)',
      }),
      expect.objectContaining({
        id: 'x',
        time: b,
        shape: 'labelDown',
        position: 'aboveBar',
        fontSize: TAG_FONT_PX,
        color: 'var(--sell-solid)',
        text: 'S',
      }),
    ])
  })

  // Review L4: the letter is a fixed size whatever the zoom, and the price is written only
  // where one candle is wide enough to hold it, so it never runs across its neighbours.
  it('writes the letter alone on narrow candles and the price where a candle holds it', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    const time = chart.barTimes()[300]
    chart.setOverlays({
      markers: [{ id: 'e', time, side: 'BUY', shape: 'tag', text: 'B 803.05', short: 'B' }],
    })
    const spacing = () => engine(chart).timeScale.barSpacing
    const text = () => layer(chart)?._markers[0].text
    chart.showBars({ from: 200, to: 400 })
    await vi.advanceTimersByTimeAsync(1_000)
    expect(spacing()).toBeLessThan(tagWidth('B 803.05'))
    expect(text()).toBe('B')
    chart.showBars({ from: 299, to: 302 })
    await vi.advanceTimersByTimeAsync(1_000)
    expect(spacing()).toBeGreaterThanOrEqual(tagWidth('B 803.05'))
    expect(text()).toBe('B 803.05')
    chart.showBars({ from: 200, to: 400 })
    await vi.advanceTimersByTimeAsync(1_000)
    expect(text()).toBe('B')
  })

  it('words a tag by the candle width, and leaves any other mark as written', () => {
    const tag = {
      id: 'e',
      time: 1,
      side: 'BUY',
      shape: 'tag',
      text: 'B 803.05',
      short: 'B',
    } as const
    expect(tagText(tag, 6)).toBe('B')
    expect(tagText(tag, tagWidth(tag.text) - 1)).toBe('B')
    expect(tagText(tag, tagWidth(tag.text))).toBe('B 803.05')
    expect(tagText({ id: 'p', time: 1, side: 'SELL', text: 'S 12.00' }, 2)).toBe('S 12.00')
    expect(tagWidth('B 25,874.30')).toBeGreaterThan(tagWidth('B 803.05'))
  })

  it('finds a mark within a finger of a tap, and nothing further away (review L4)', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    chart.showBars({ from: 280, to: 320 })
    await vi.advanceTimersByTimeAsync(1_000)
    const bars = (chart as unknown as { bars: Bar[] }).bars
    const [buy, sell] = [bars[300], bars[310]]
    chart.setOverlays({
      markers: [
        { id: 'e', time: buy.time, side: 'BUY', shape: 'tag', text: 'B', short: 'B' },
        { id: 'x', time: sell.time, side: 'SELL', shape: 'tag', text: 'S', short: 'S' },
      ],
    })
    await vi.advanceTimersByTimeAsync(1_000)
    const x = (bar: Bar) => chart.barX(bar) as number
    const y = (price: number) => engine(chart).priceToCoordinate(price, 0) as number
    expect(chart.markerAt(x(buy), y(buy.low) + 14)).toBe('e')
    expect(chart.markerAt(x(buy) + 20, y(buy.low) + 30)).toBe('e')
    expect(chart.markerAt(x(sell) - 10, y(sell.high) - 14)).toBe('x')
    expect(chart.markerAt(x(buy), y(buy.low) + 60)).toBeNull()
    expect(chart.markerAt(x(buy) + 40, y(buy.low) + 14)).toBeNull()
  })

  it('says when a new candle opens after the load, never during one', async () => {
    const { chart } = mountChart()
    const heard = vi.fn()
    chart.onBars(heard)
    await chart.load('SBIN', 'NSE', '1m')
    expect(heard).not.toHaveBeenCalled()
    const last = chart.barTimes().at(-1) as number
    const apply = (chart as unknown as { applyLiveBar(update: unknown): void }).applyLiveBar.bind(
      chart
    )
    apply({ bar: { time: last + 60, open: 1, high: 1, low: 1, close: 1 }, isNew: true })
    expect(heard).toHaveBeenCalledTimes(1)
    expect(chart.barTimes().at(-1)).toBe(last + 60)
  })
})

describe('room for the marks', () => {
  it('keeps room above and below the candles while marks are laid, and gives it back after', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    const scale = () => (chart as unknown as { price: SeriesApi }).price.priceScale().options
    expect([scale().marginTop, scale().marginBottom]).toEqual([PLAIN_ROOM, PLAIN_ROOM])
    const time = chart.barTimes()[10]
    chart.setOverlays({ markers: [{ id: 'e', time, side: 'BUY', shape: 'tag', text: 'B' }] })
    expect([scale().marginTop, scale().marginBottom]).toEqual([MARK_ROOM, MARK_ROOM])
    await chart.load('SBIN', 'NSE', '5m') // a new chart keeps it while the marks stay
    expect([scale().marginTop, scale().marginBottom]).toEqual([MARK_ROOM, MARK_ROOM])
    chart.setOverlays({ markers: [] })
    expect([scale().marginTop, scale().marginBottom]).toEqual([PLAIN_ROOM, PLAIN_ROOM])
  })
})

describe('the plain marks', () => {
  it('stay as they were: arrows, a size down', async () => {
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '1m')
    const time = chart.barTimes()[10]
    chart.setOverlays({ markers: [{ id: 'p', time, side: 'SELL', text: 'S' }] })
    expect(layer(chart)?._markers).toEqual([
      expect.objectContaining({ shape: 'arrowDown', size: 'small', position: 'aboveBar' }),
    ])
  })
})

/** The journal's trade of SBIN between two times, as trip_get answers it. */
function trade(entryMs: number, exitMs: number) {
  const leg = (role: string, action: string, at: number, price: number) => ({
    trade_id: `${role}-1`,
    order_id: `${role}-o`,
    role,
    action,
    qty: 10,
    price_u: Math.round(price * 10_000),
    exch_ts_ms: at,
    day: 20261020,
    source: 'stream_read',
  })
  return {
    enabled: true,
    data: {
      id: 7,
      mode: 'live',
      exchange: 'NSE',
      symbol: 'SBIN',
      side: 'long',
      status: 'closed',
      opened_ms: entryMs,
      closed_ms: exitMs,
      legs: [leg('entry', 'BUY', entryMs, 803.05), leg('exit', 'SELL', exitMs, 809.4)],
    },
  }
}

/** The page's part: a chosen timeframe loads the chart, as ChartPage's effect does. */
function view(chart: PhoneChart) {
  const picked: string[] = []
  const hook = renderHook(() =>
    useTripMarkers({
      owner: chart,
      tripId: 7,
      symbol: 'SBIN',
      exchange: 'NSE',
      decimals: 2,
      setTimeframe: (interval) => {
        picked.push(interval)
        void chart.load('SBIN', 'NSE', interval)
      },
    })
  )
  return { hook, picked }
}

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50)
  })
}

describe('a trade from its sheet, on the real engine', () => {
  it('opens on minute candles with both marks and the entry in view', async () => {
    const entry = ist(10, 2, 5)
    const exit = ist(10, 41, 50)
    call.mockResolvedValue(trade(entry, exit))
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '15m') // the timeframe saved on the phone
    const { picked } = view(chart)
    await flush()
    expect(picked).toEqual(['1m'])
    const marks = layer(chart)?._markers ?? []
    expect(marks.map((m) => [m.text, m.time, m.shape])).toEqual([
      ['B', ist(10, 2) / 1000, 'labelUp'],
      ['S', ist(10, 41) / 1000, 'labelDown'],
    ])
    const times = chart.barTimes()
    const range = engine(chart).getVisibleLogicalRange()
    expect(range).not.toBeNull()
    const [first, last] = [times.indexOf(ist(10, 2) / 1000), times.indexOf(ist(10, 41) / 1000)]
    expect(range?.from).toBeLessThanOrEqual(first)
    expect(range?.to).toBeGreaterThanOrEqual(last)
  })

  it('moves a trade older than the minute candles to daily ones, and says so', async () => {
    const entry = ist(10, 2) - 20 * 86_400_000
    call.mockResolvedValue(trade(entry, entry + 30 * 60_000))
    const { chart } = mountChart()
    await chart.load('SBIN', 'NSE', '5m')
    const { hook, picked } = view(chart)
    await flush()
    await flush()
    expect(picked).toEqual(['1m', 'D'])
    expect(hook.result.current.notice).toBe(`${OLDER_THAN_HISTORY} ${SHOWN_DAILY}`)
    const day = (ist(0, 0) - 20 * 86_400_000) / 1000
    expect((layer(chart)?._markers ?? []).map((m) => [m.text, m.time])).toEqual([
      ['B', day],
      ['S', day],
    ])
  })
})
