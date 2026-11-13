/**
 * Open trades moving with ticks, through the shared feed's harness: real subscriptions, ticks
 * pushed by hand, frames run by hand. The L vectors run through the hook as well as the function.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { trip } from './fixtures'
import { pricedTrips, useTripLive } from './useTripLive'

const VECTORS = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'journal', 'tests', 'vectors', 'metrics.json'),
    'utf8'
  )
).live as Array<{
  id: string
  side: 'long' | 'short'
  qty: number
  entry_avg_u: number
  ticks: Array<{ ltp_u: number; pnl_paise: number }>
}>

beforeEach(() => {
  fakeClock()
  feed.reset()
  frames.install()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useTripLive', () => {
  for (const vector of VECTORS) {
    it(`${vector.id} through the hook, a tick at a time`, () => {
      const trips = [
        trip({
          id: 1,
          symbol: `T${vector.id}`,
          side: vector.side,
          entry_qty: vector.qty,
          qty_max: vector.qty,
          entry_avg_u: vector.entry_avg_u,
        }),
      ]
      const { result } = renderHook(() => useTripLive({ trips, marketOpen: () => true }))
      act(() => frames.flush())
      expect(result.current.pnl.get(1)?.pnl_paise).toBeNull()
      for (const tick of vector.ticks) {
        act(() => feed.push(`T${vector.id}`, 'NSE', { ltp: tick.ltp_u / 10_000 }))
        act(() => frames.flush())
        expect(result.current.pnl.get(1)?.pnl_paise).toBe(tick.pnl_paise)
      }
    })
  }

  it('says Real-time while ticks arrive, and Waiting for prices before the first', () => {
    const trips = [trip({ id: 1 })]
    const { result } = renderHook(() => useTripLive({ trips, marketOpen: () => true }))
    act(() => frames.flush())
    expect(result.current.line.kind).toBe('waiting')
    act(() => feed.push('SBIN', 'NSE', { ltp: 101.5 }))
    act(() => frames.flush())
    expect(result.current.line.kind).toBe('live')
    expect(result.current.pnl.get(1)).toMatchObject({ pnl_paise: 9750, live: true, stale: false })
  })

  it('says Market closed, and has no line at all with nothing open', () => {
    const open = renderHook(() =>
      useTripLive({ trips: [trip({ id: 1 })], marketOpen: () => false })
    )
    act(() => frames.flush())
    expect(open.result.current.line.kind).toBe('closed')
    const none = renderHook(() => useTripLive({ trips: [], marketOpen: () => true }))
    act(() => frames.flush())
    expect(none.result.current.line.kind).toBe('idle')
    expect(feed.live()).toEqual(['NSE:SBIN:LTP'])
  })

  it('a price from the REST fallback is a price but never Real-time', () => {
    const { result } = renderHook(() =>
      useTripLive({ trips: [trip({ id: 1 })], marketOpen: () => true })
    )
    act(() => frames.flush())
    act(() => feed.push('SBIN', 'NSE', { ltp: 99 }, 'rest'))
    act(() => frames.flush())
    expect(result.current.pnl.get(1)).toMatchObject({ pnl_paise: -6500, live: false })
    expect(result.current.line.kind).not.toBe('live')
  })

  it('greys the figure when ticks are due and have stopped, keeping the number', () => {
    const { result } = renderHook(() =>
      useTripLive({ trips: [trip({ id: 1 })], marketOpen: () => true })
    )
    act(() => frames.flush())
    act(() => feed.push('SBIN', 'NSE', { ltp: 101.5 }, 'websocket', Date.now() - 60_000))
    act(() => frames.flush())
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(result.current.pnl.get(1)).toMatchObject({ pnl_paise: 9750, stale: true })
  })

  it('subscribes once per instrument, and lets go of it with the trade', () => {
    const two = [trip({ id: 1 }), trip({ id: 2, entry_avg_u: 1_020_000 })]
    const { rerender } = renderHook(({ list }) => useTripLive({ trips: list }), {
      initialProps: { list: two },
    })
    act(() => frames.flush())
    expect(feed.live()).toEqual(['NSE:SBIN:LTP'])
    rerender({ list: [] })
    act(() => frames.flush())
    expect(feed.live()).toEqual([])
  })

  it('prices only open trades that still hold a quantity', () => {
    const list = [
      trip({ id: 1 }),
      trip({ id: 2, entry_qty: 10, exit_qty: 10 }),
      trip({ id: 3, status: 'closed' }),
    ]
    expect(pricedTrips(list).map((t) => t.id)).toEqual([1])
  })
})
