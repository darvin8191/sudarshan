/**
 * v0.17.0 phone review M2 (M8.2), the hook: a contract the server refused on its own expiry day
 * may only have been missing while the morning login replaced the contract list. It reads
 * "Expired" until it is asked again two minutes later, is asked no more often than that, and no
 * longer once its exchange's session for the day is over. On the market-data test harness (real
 * subscriptions) with a counted REST read; every name is made up.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const server = vi.hoisted(() => ({ listless: true, calls: [] as string[] }))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: vi.fn(
      async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => {
        server.calls.push(symbols.map((each) => each.symbol).join(','))
        if (server.listless)
          throw Object.assign(new Error('Request failed with status code 400'), {
            response: { status: 400, data: { status: 'error', invalid_symbols: symbols } },
          })
        return {
          status: 'success',
          results: symbols.map((each) => ({ ...each, data: { ltp: 120, prev_close: 100 } })),
        }
      }
    ),
  },
}))

import type { WatchlistItem } from '@/api/watchlist'
import { frames, manualClock } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { useLiveRows } from './useLiveRows'

/** Expires on 24 Nov 2026, the day of these tests: it trades all day. */
const DUE: WatchlistItem = { id: 1, symbol: 'NIFTY24NOV2625900CE', exchange: 'NFO', position: 0 }
const at = (hhmm: string) => Date.parse(`2026-11-24T${hhmm}:00+05:30`)
const market = { open: false as boolean | null }

const settle = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
    frames.flush()
  })
}
const pass = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
    frames.flush()
  })
}
const mount = () =>
  renderHook(() => useLiveRows({ items: [DUE], apiKey: 'k', marketOpen: () => market.open }))

beforeEach(() => {
  feed.reset()
  frames.install()
  manualClock()
  server.listless = true
  server.calls = []
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('a contract refused on its own expiry day (review M2)', () => {
  it('reads Expired only until it is asked again two minutes later, then is read and followed', async () => {
    vi.setSystemTime(at('08:50')) // the morning login is loading the contract list
    market.open = false
    const { result } = mount()
    await settle()
    expect(server.calls).toEqual(['NIFTY24NOV2625900CE'])
    expect(result.current.rows[0].expired).toBe('2026-11-24')
    expect(feed.live().some((key) => key.includes(DUE.symbol))).toBe(false)
    server.listless = false // the list is back
    await pass(118_000)
    expect(server.calls).toHaveLength(1)
    await pass(3_000)
    expect(server.calls).toEqual(['NIFTY24NOV2625900CE', 'NIFTY24NOV2625900CE'])
    expect(result.current.rows[0].expired).toBeUndefined()
    expect(result.current.rows[0].view.ltp).toBe(120)
    expect(feed.live()).toContain(`NFO:${DUE.symbol}:LTP`)
  })

  it('is asked every two minutes at most while still refused, and no more once its session is over', async () => {
    vi.setSystemTime(at('15:26'))
    market.open = true
    const { result } = mount()
    await settle()
    expect(result.current.rows[0].expired).toBe('2026-11-24')
    await pass(121_000)
    expect(server.calls).toHaveLength(2) // asked again, refused again
    await pass(60_000)
    expect(server.calls).toHaveLength(2) // not sooner than two minutes
    await pass(61_000)
    expect(server.calls).toHaveLength(3)
    expect(result.current.rows[0].expired).toBe('2026-11-24')
    // 15:30: the market closes for the day; the contract has expired whatever is asked.
    market.open = false
    await pass(10 * 60_000)
    expect(server.calls).toHaveLength(3)
    expect(result.current.rows[0].expired).toBe('2026-11-24')
  })
})
