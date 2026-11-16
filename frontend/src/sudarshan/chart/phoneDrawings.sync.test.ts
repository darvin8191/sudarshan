/**
 * The phone chart's drawings settled with the server's copy of record (M4.18), on the real chart
 * engine and its draw tier: switched off, nothing changes; on, an instrument's drawings from
 * another device appear when it opens, a change made here is sent under the engine's instrument
 * key, and a conflict shows the newer copy with the words and the way back. A fake server stands
 * in for the bridge; nothing reaches a network.
 */
import { type Bar, SessionCalendar } from 'openalgo-charts'
import type { DrawingController } from 'openalgo-charts/draw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DocKind, ServerDoc, WriteResult } from '@/sudarshan/charts/api'
import { sharedChartsApi } from '@/sudarshan/charts/api'
import { chartSyncNotices, forgetDocumentSyncs } from '@/sudarshan/charts/docSync'
import { ist } from '@/test/tapes/scenarios'
import type { PhoneChart } from './phoneChart'
import { phoneDrawingsKey, serverDrawingsKey } from './phoneDrawings'
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

const levelAt = (price: number, id = `l${price}`) => ({
  id,
  tool: 'horizontal-line',
  points: [{ time: bars()[10].time, price }],
  style: {},
  paneIndex: 0,
  zIndex: 0,
})

/** The bridge as the server would answer it, kept in memory. */
const server = {
  on: true,
  next: 1_794_000_000_000,
  docs: new Map<string, { revision: number; body: unknown }>(),
  writes: [] as Array<{
    key: string
    base: number
    body: { drawings: Array<{ points: Array<{ price: number }> }> }
  }>,
  saves(key: string, body: unknown) {
    this.docs.set(key, { revision: ++this.next, body })
  },
}

const controller = (chart: PhoneChart) =>
  (chart.drawings as unknown as { ctl: DrawingController | null }).ctl
const prices = (chart: PhoneChart) =>
  controller(chart)
    ?.drawings()
    .map((d) => d.points[0].price)
    .sort()

beforeEach(() => {
  localStorage.clear()
  forgetDocumentSyncs()
  installHost(ist(10, 0, 30))
  host.replies.history = () => historyOf(bars())
  server.on = true
  server.docs.clear()
  server.writes.length = 0
  vi.spyOn(sharedChartsApi, 'enabled').mockImplementation(async () => server.on)
  vi.spyOn(sharedChartsApi, 'read').mockImplementation(async (kind: DocKind, key, have) => {
    const doc = server.docs.get(key)
    if (!doc) return { ok: true, doc: null }
    const out: ServerDoc = { kind, key, revision: doc.revision, updated_ms: 1 }
    if (have !== doc.revision) out.body = structuredClone(doc.body)
    return { ok: true, doc: out }
  })
  vi.spyOn(sharedChartsApi, 'write').mockImplementation(
    async (kind: DocKind, key, base, body): Promise<WriteResult> => {
      server.writes.push({ key, base, body: structuredClone(body) as never })
      const doc = server.docs.get(key)
      if ((doc?.revision ?? 0) !== base)
        return {
          ok: false,
          stale: doc ? { kind, key, revision: doc.revision, updated_ms: 1, body: doc.body } : null,
        }
      server.saves(key, structuredClone(body))
      return { ok: true, doc: { kind, key, revision: server.next, updated_ms: 1 } }
    }
  )
})
afterEach(() => {
  removeHost()
  vi.restoreAllMocks()
  for (let n = chartSyncNotices.latest(); n; n = chartSyncNotices.latest())
    chartSyncNotices.clear(n.id)
})

async function opened(symbol = 'SBIN') {
  const view = mountChart()
  await view.chart.load(symbol, 'NSE', '5m')
  await vi.advanceTimersByTimeAsync(0)
  return view
}

describe('the drawings of an instrument across devices', () => {
  it('uses the engine’s own instrument key for the server', () => {
    expect(serverDrawingsKey(phoneDrawingsKey('SBIN', 'NSE') ?? '')).toBe('NSE:SBIN')
    expect(serverDrawingsKey(phoneDrawingsKey('NIFTY 50', 'NSE_INDEX') ?? '')).toBe(
      'NSE_INDEX:NIFTY%2050'
    )
  })

  it('switched off, a drawing is kept on this phone and nothing is sent', async () => {
    server.on = false
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    controller(chart)?.add(levelAt(815))
    await vi.advanceTimersByTimeAsync(0)
    expect(server.writes).toEqual([])
    expect(sharedChartsApi.read).not.toHaveBeenCalled()
    expect(localStorage.getItem('sudarshan-chart-draw:NSE:SBIN')).toContain('815')
  })

  it('shows the drawings another device saved when the instrument opens', async () => {
    server.saves('NSE:SBIN', { version: 2, drawings: [levelAt(812)] })
    const { chart } = await opened()
    await vi.waitFor(() => expect(prices(chart)).toEqual([812]))
    expect(chart.drawings.state().count).toBe(1)
    expect(localStorage.getItem('sudarshan-chart-draw:NSE:SBIN')).toContain('812')
    expect(server.writes).toEqual([]) // taken, not sent back
  })

  it('sends a drawing made here, and the next one from the revision the first made', async () => {
    const { chart } = await opened()
    await chart.drawings.setTool(null)
    controller(chart)?.add(levelAt(815))
    await vi.advanceTimersByTimeAsync(0)
    controller(chart)?.add(levelAt(820))
    await vi.advanceTimersByTimeAsync(0)
    expect(server.writes.map((w) => [w.key, w.body.drawings.length])).toEqual([
      ['NSE:SBIN', 1],
      ['NSE:SBIN', 2],
    ])
    expect(server.writes[1].base).toBe(server.next - 1)
  })

  it('in a conflict shows the other device’s copy, says so, and puts this phone’s back when asked', async () => {
    const first = await opened()
    await first.chart.drawings.setTool(null)
    controller(first.chart)?.add(levelAt(815))
    await vi.advanceTimersByTimeAsync(0)
    first.chart.destroy()
    // Another device adds a level; this phone, offline meanwhile, adds one of its own.
    server.saves('NSE:SBIN', { version: 2, drawings: [levelAt(815), levelAt(830, 'tablet')] })
    const mine = { version: 2, drawings: [levelAt(815), levelAt(800, 'phone')] }
    localStorage.setItem('sudarshan-chart-draw:NSE:SBIN', JSON.stringify(mine))

    const { chart } = await opened()
    await vi.waitFor(() => expect(prices(chart)).toEqual([815, 830]))
    const notice = chartSyncNotices.latest()
    expect(notice?.text).toBe(
      'The drawings on SBIN were changed on another device. That copy is shown; the change made on this phone was not saved.'
    )
    notice?.action?.run()
    await vi.waitFor(() => expect(prices(chart)).toEqual([800, 815]))
    await vi.waitFor(() =>
      expect(
        server.writes
          .at(-1)
          ?.body.drawings.map((d) => d.points[0].price)
          .sort()
      ).toEqual([800, 815])
    )
    expect(chartSyncNotices.latest()?.tone).toBe('kept')
  })
})
