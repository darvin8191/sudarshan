/**
 * Live lines drawn once per animation frame (M8.2), on the shared market-data
 * manager's test harness: real subscriptions, ticks pushed by hand, frames run
 * by hand.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

import type { SubscriptionMode } from '@/lib/MarketDataManager'
import { frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { type Instrument, useFrameLines } from './useFrameLines'

const SBIN = { symbol: 'SBIN', exchange: 'NSE' }
const INFY = { symbol: 'INFY', exchange: 'NSE' }

let renders = 0

function mount(instruments: Instrument[], mode: SubscriptionMode = 'LTP') {
  const view = renderHook(
    ({ list, enabled }: { list: Instrument[]; enabled: boolean }) => {
      renders += 1
      return useFrameLines({ instruments: list, mode, enabled })
    },
    { initialProps: { list: instruments, enabled: true } }
  )
  act(() => frames.flush())
  return view
}

const push = (instrument: Instrument, ltp: number, source: 'websocket' | 'rest' = 'websocket') =>
  feed.push(instrument.symbol, instrument.exchange, { ltp }, source)

const frame = () => act(() => frames.flush())

beforeEach(() => {
  feed.reset()
  frames.install()
  renders = 0
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useFrameLines', () => {
  it('draws a burst of ticks once, on the next frame, with the newest of each', () => {
    const { result } = mount([SBIN, INFY])
    const before = renders

    act(() => {
      for (let i = 0; i < 20; i += 1) push(i % 2 === 0 ? SBIN : INFY, 100 + i)
    })
    expect(renders).toBe(before)
    expect(result.current.lines.size).toBe(0)

    frame()
    expect(renders).toBe(before + 1)
    expect(result.current.lines.get('NSE:SBIN')?.data.ltp).toBe(118)
    expect(result.current.lines.get('NSE:INFY')?.data.ltp).toBe(119)

    // Nothing new, nothing drawn.
    frame()
    expect(renders).toBe(before + 1)
  })

  it("keeps each tick's direction, so a frame shows the last tick's, not the frame's net move", () => {
    const { result } = mount([SBIN])
    const direction = () => result.current.directions.get('NSE:SBIN')

    act(() => push(SBIN, 100))
    frame()
    // One level is no move yet.
    expect(direction()).toBeUndefined()

    act(() => {
      push(SBIN, 101)
      push(SBIN, 99)
      push(SBIN, 99.5)
    })
    frame()
    // Net move over the frame is down (100 to 99.5); the last tick was up.
    expect(direction()).toBe('up')

    act(() => push(SBIN, 99.5))
    frame()
    expect(direction()).toBe('up')

    act(() => push(SBIN, 99))
    frame()
    expect(direction()).toBe('down')
  })

  it('gives a REST update no direction, and forgets directions on a reconnect', () => {
    const { result } = mount([SBIN])
    act(() => {
      push(SBIN, 100)
      push(SBIN, 101)
    })
    frame()
    expect(result.current.directions.get('NSE:SBIN')).toBe('up')

    act(() => push(SBIN, 102, 'rest'))
    frame()
    expect(result.current.directions.get('NSE:SBIN')).toBeUndefined()

    act(() => {
      push(SBIN, 103)
      push(SBIN, 104)
    })
    frame()
    expect(result.current.directions.get('NSE:SBIN')).toBe('up')

    act(() => feed.setState({ connectionEpoch: 2 }))
    frame()
    expect(result.current.feed.epoch).toBe(2)
    expect(result.current.directions.get('NSE:SBIN')).toBeUndefined()
  })

  it('subscribes by difference: a join or a leave leaves the others subscribed and drawn', () => {
    const { result, rerender } = mount([SBIN])
    act(() => push(SBIN, 812))
    frame()

    rerender({ list: [SBIN, INFY], enabled: true })
    frame()
    // The harness empties a line once nothing subscribes to it, as the
    // manager does: re-subscribing SBIN to add INFY would have lost its level.
    expect(feed.opened.filter((key) => key === 'NSE:SBIN:LTP')).toHaveLength(1)
    expect(feed.closed).toEqual([])
    expect(result.current.lines.get('NSE:SBIN')?.data.ltp).toBe(812)

    rerender({ list: [INFY], enabled: true })
    frame()
    expect(feed.live()).toEqual(['NSE:INFY:LTP'])
    expect(result.current.lines.has('NSE:SBIN')).toBe(false)
  })

  it('starts an instrument that rejoins afresh: no line and no direction from before', () => {
    const { result, rerender } = mount([SBIN, INFY])
    act(() => {
      push(SBIN, 101)
      push(SBIN, 102)
    })
    frame()
    expect(result.current.directions.get('NSE:SBIN')).toBe('up')

    rerender({ list: [INFY], enabled: true })
    frame()
    expect(result.current.directions.has('NSE:SBIN')).toBe(false)

    rerender({ list: [INFY, SBIN], enabled: true })
    act(() => push(SBIN, 96))
    frame()
    expect(result.current.lines.get('NSE:SBIN')?.data.ltp).toBe(96)
    expect(result.current.directions.has('NSE:SBIN')).toBe(false)
  })

  it('takes a level the manager already holds at once', () => {
    feed.push('SBIN', 'NSE', { ltp: 810 })
    const { result } = mount([SBIN])
    expect(result.current.lines.get('NSE:SBIN')?.data.ltp).toBe(810)
  })

  it('subscribes in the mode asked for', () => {
    mount([SBIN], 'Depth')
    expect(feed.live()).toEqual(['NSE:SBIN:Depth'])
  })

  it('subscribes nothing while not enabled', () => {
    const { rerender } = mount([SBIN])
    rerender({ list: [SBIN], enabled: false })
    expect(feed.live()).toEqual([])
  })

  it('releases every subscription and the waiting frame on unmount', () => {
    const { unmount } = mount([SBIN, INFY])
    act(() => push(SBIN, 100))
    expect(frames.pending).toBe(1)
    unmount()
    expect(feed.live()).toEqual([])
    expect(frames.pending).toBe(0)
  })

  it('follows the socket: signed in, the REST fallback, sign-ins', () => {
    const { result } = mount([SBIN])
    expect(result.current.feed).toEqual({ connected: true, fallback: false, epoch: 1 })

    act(() => feed.setState({ isAuthenticated: false, isConnected: false }))
    expect(result.current.feed.connected).toBe(false)
    act(() => feed.setState({ isFallbackMode: true }))
    expect(result.current.feed.fallback).toBe(true)
  })
})
