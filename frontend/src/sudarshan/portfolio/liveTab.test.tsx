/**
 * A tab moved by ticks, frame by frame, against sudarshan/portfolio/tests/vectors/
 * valuation_ticks.json (written by make_pd2.py, run on the Python core by test_portfolio_ticks.py),
 * and the hook that feeds it: the cap, and "Real-time" only while ticks arrive.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'
import {
  LIVE_CAP,
  type LiveRowInput,
  liveIndexes,
  liveInstruments,
  moveTab,
  type Tick,
  tickOf,
} from './liveTab'
import { feedPriceU } from './money'
import type { ValuationFixed } from './valuation'

const mocks = vi.hoisted(() => ({ lines: new Map(), connected: true, seen: [] as unknown[] }))
vi.mock('@/sudarshan/live/useFrameLines', () => ({
  useFrameLines: (args: { instruments: unknown[] }) => {
    mocks.seen.push(args.instruments)
    return {
      lines: mocks.lines,
      directions: new Map(),
      feed: { connected: mocks.connected, fallback: false, epoch: 1 },
    }
  },
}))

import { useTabLive } from './useTabLive'

const FILE = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', 'valuation_ticks.json'),
    'utf8'
  )
) as { replays: any[] }

describe('valuation_ticks.json, frame by frame', () => {
  describe.each(FILE.replays)('replay $id: $title', (replay) => {
    it('every frame is the Python value for the same ticks', () => {
      const ticks = new Map<string, Tick>()
      for (const frame of replay.frames) {
        for (const t of frame.ticks) {
          // The hook keeps the newest good tick of an instrument; a price of nothing is not one.
          if (feedPriceU(t.ltp) === null) continue
          ticks.set(t.key, { ltp: t.ltp, prev_close: t.prev_close, at: t.at, live: t.live })
        }
        const got = moveTab(replay.rows, replay.fixed, ticks, replay.read_at, replay.cap)
        expect(got.totals, frame.note).toEqual(frame.expect.totals)
        expect(got.moved, frame.note).toEqual(frame.expect.moved)
        expect(
          got.rows.map((r) => r.price.price_u),
          frame.note
        ).toEqual(frame.expect.price_u)
        expect(
          got.rows.map((r) => r.price.prev_close_u),
          frame.note
        ).toEqual(frame.expect.prev_close_u)
      }
    })
  })

  it('a row a tick did not move is the very object the service sent', () => {
    const replay = FILE.replays[0]
    const got = moveTab(replay.rows, replay.fixed, new Map(), replay.read_at)
    got.rows.forEach((row, n) => {
      expect(row).toBe(replay.rows[n])
    })
  })
})

const listed = (n: number, qty = 1_000_000): LiveRowInput => ({
  asset: { kind: 'listed', exchange: 'NSE', symbol: `S${n}` },
  today_mode: 'ok',
  price: { price_u: 1_000_000, prev_close_u: 1_000_000 },
  lines: [
    {
      account_id: 1,
      qty_u: qty,
      open_qty_u: qty,
      cost_paise: 100,
      in_today_paise: 0,
      out_today_paise: 0,
      over_year_qty_u: 0,
    },
  ],
})

const FIXED: ValuationFixed = {
  realised_paise: 0,
  income_paise: 0,
  fees_paise: 0,
  money_in_paise: 0,
  money_out_paise: 0,
  closed_today_paise: 0,
  closed_today_base_paise: 0,
}

describe('the cap', () => {
  const rows = Array.from({ length: 250 }, (_, n) => listed(n))

  it('subscribes at most 200 instruments, the first 200 held listed rows', () => {
    expect(LIVE_CAP).toBe(200)
    const picked = liveInstruments(rows)
    expect(picked).toHaveLength(200)
    expect(picked[0]).toEqual({ symbol: 'S0', exchange: 'NSE' })
    expect(picked[199]).toEqual({ symbol: 'S199', exchange: 'NSE' })
  })

  it('skips what cannot tick: a fund, a manual value, a holding sold out', () => {
    const mixed: LiveRowInput[] = [
      { ...listed(1), asset: { kind: 'mf', exchange: null, symbol: null } },
      { ...listed(2), asset: { kind: 'manual', exchange: null, symbol: null } },
      listed(3, 0),
      listed(4),
    ]
    expect([...liveIndexes(mixed)]).toEqual([3])
  })

  it('rows past the cap keep the service price and are counted', () => {
    const ticks = new Map<string, Tick>([
      ['NSE:S0', { ltp: 110, prev_close: 100, at: 2000, live: true }],
      ['NSE:S249', { ltp: 110, prev_close: 100, at: 2000, live: true }],
    ])
    const got = moveTab(rows, FIXED, ticks, 1000)
    expect(got.moved[0]).toBe(true)
    expect(got.moved[249]).toBe(false)
    expect(got.rows[249]).toBe(rows[249])
    expect(got.capped).toBe(50)
  })
})

const line = (ltp: number, at: number, live = true): SymbolData => ({
  symbol: 'S0',
  exchange: 'NSE',
  data: { ltp, prev_close: 100 },
  lastUpdate: at,
  updateSource: live ? 'websocket' : 'rest',
})

describe('tickOf', () => {
  it('reads a cache line, and refuses one with no price', () => {
    expect(tickOf(line(104, 5))).toEqual({ ltp: 104, prev_close: 100, at: 5, live: true })
    expect(tickOf(line(104, 5, false))?.live).toBe(false)
    expect(tickOf(line(0, 5))).toBeNull()
    expect(tickOf(undefined)).toBeNull()
  })
})

describe('useTabLive', () => {
  const rows = [listed(0)]
  const open = () => true

  function run(readAt: number) {
    return renderHook(() => useTabLive({ rows, fixed: FIXED, readAt, marketOpen: open })).result
      .current
  }

  it('says Real-time only while WebSocket ticks arrive', () => {
    mocks.connected = true
    mocks.lines = new Map()
    expect(run(0).line.kind).toBe('waiting')

    mocks.lines = new Map([['NSE:S0', line(104, Date.now())]])
    const live = run(0)
    expect(live.line.kind).toBe('live')
    expect(live.moved).toEqual([true])
    expect(live.totals.value_paise).toBe(10_400) // 1 share at 104.00
  })

  it('a REST answer moves nothing and is not Real-time', () => {
    mocks.lines = new Map([['NSE:S0', line(104, Date.now(), false)]])
    const got = run(0)
    expect(got.moved).toEqual([false])
    expect(got.line.kind).not.toBe('live')
  })

  it('a tick older than the read moves nothing', () => {
    const now = Date.now()
    mocks.lines = new Map([['NSE:S0', line(104, now - 1000)]])
    expect(run(now).moved).toEqual([false])
  })

  it('a socket that is down says so, not Real-time', () => {
    mocks.connected = false
    mocks.lines = new Map()
    expect(run(0).line.kind).toBe('connecting')
    mocks.connected = true
  })

  it('subscribes the capped instruments only', () => {
    mocks.seen.length = 0
    const many = Array.from({ length: 250 }, (_, n) => listed(n))
    renderHook(() => useTabLive({ rows: many, fixed: FIXED, readAt: 0, marketOpen: open }))
    expect(mocks.seen.at(-1)).toHaveLength(200)
  })
})
