/**
 * useLiveQuote under rule 7: the order ticket and the modify dialog read one
 * REST snapshot when they open and then follow the ticks. Nothing is asked on
 * a timer while the socket is healthy; the snapshot is read again when the
 * market-data socket signs in again; the newer of the tick and the snapshot
 * wins; and the previous close is never taken from a tick's running bar.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getQuotes = vi.fn()
const getDepth = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getQuotes: (...args: unknown[]) => getQuotes(...args),
    getDepth: (...args: unknown[]) => getDepth(...args),
  },
}))
vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ apiKey: 'key' }) }))
const calendar = {
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))

import { feed } from '@/test/marketDataHarness'
import { useLiveQuote } from './useLiveQuote'

const QUOTE = {
  ltp: 130,
  open: 120,
  high: 135,
  low: 118,
  prev_close: 125,
  volume: 10000,
  oi: 50000,
  bid: 129.9,
  ask: 130.1,
}

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
  getQuotes.mockReset().mockResolvedValue({ status: 'success', data: QUOTE })
  getDepth
    .mockReset()
    .mockResolvedValue({ status: 'success', data: { bids: [], asks: [], ltp: 130 } })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useLiveQuote', () => {
  it('reads one snapshot when it opens and nothing on a timer while the socket is healthy', async () => {
    renderHook(() => useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' }))
    await settle()
    expect(getQuotes).toHaveBeenCalledTimes(1)

    await settle(5 * 60_000)
    expect(getQuotes).toHaveBeenCalledTimes(1)
  })

  it('reads the snapshot again once the market-data socket signs in again', async () => {
    renderHook(() => useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' }))
    await settle()
    act(() => feed.setState({ isAuthenticated: false, connectionState: 'connecting' }))
    await settle(5_000)
    act(() =>
      feed.setState({ isAuthenticated: true, connectionState: 'authenticated', connectionEpoch: 2 })
    )
    await settle()
    expect(getQuotes).toHaveBeenCalledTimes(2)
  })

  it('follows a newer tick, and fills from the snapshot only what the tick does not carry', async () => {
    const { result } = renderHook(() =>
      useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' })
    )
    await settle(1_000)
    expect(result.current.data.ltp).toBe(130)

    // A Quote tick whose `close` is today's running bar: never the previous close.
    act(() => feed.push('NIFTY28OCT2525900CE', 'NFO', { ltp: 133, close: 133 }))
    expect(result.current.data.ltp).toBe(133)
    expect(result.current.data.close).toBe(125)
    // Change from the previous close (133 - 125), not 0 from the running bar.
    expect(result.current.data.change).toBeCloseTo(8)
    expect(result.current.data.oi).toBe(50000)
    expect(result.current.feed.lastTickAt).not.toBeNull()
  })

  it("takes a tick's own previous close and open interest when it carries them", async () => {
    const { result } = renderHook(() =>
      useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' })
    )
    await settle(1_000)
    act(() =>
      feed.push('NIFTY28OCT2525900CE', 'NFO', { ltp: 133, prev_close: 126, oi: 51000 } as never)
    )
    expect(result.current.data.close).toBe(126)
    expect(result.current.data.oi).toBe(51000)
  })

  // The completed Upstox feed: Quote and Depth ticks carry prev_close, volume
  // and oi, and `close` is the previous close or 0 when it is not known.
  it('works out the change from the previous close, not from a change the tick carries', async () => {
    const { result } = renderHook(() =>
      useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' })
    )
    await settle(1_000)
    act(() =>
      feed.push('NIFTY28OCT2525900CE', 'NFO', {
        ltp: 125,
        close: 0,
        prev_close: 125,
        change: 3,
        change_percent: 2.4,
      } as never)
    )
    expect(result.current.data.close).toBe(125)
    expect(result.current.data.change).toBe(0)
    expect(result.current.data.changePercent).toBe(0)
  })

  it('keeps the last open, high, low and volume when an LTP-only tick follows', async () => {
    const { result } = renderHook(() =>
      useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' })
    )
    await settle(1_000)
    act(() =>
      feed.push('NIFTY28OCT2525900CE', 'NFO', {
        ltp: 131,
        open: 121,
        high: 136,
        low: 119,
        volume: 12000,
        prev_close: 125,
      } as never)
    )
    act(() => feed.push('NIFTY28OCT2525900CE', 'NFO', { ltp: 132 }))
    expect(result.current.data).toMatchObject({
      ltp: 132,
      open: 121,
      high: 136,
      low: 119,
      volume: 12000,
      close: 125,
    })
  })

  it('says nothing has traded yet for a contract with no tick and no volume today', async () => {
    getQuotes.mockResolvedValue({ status: 'success', data: { ...QUOTE, volume: 0 } })
    const { result } = renderHook(() =>
      useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' })
    )
    await settle(1_000)
    expect(result.current.feed.noTrade).toBe(true)

    act(() => feed.push('NIFTY28OCT2525900CE', 'NFO', { ltp: 131, volume: 65 }))
    expect(result.current.feed.noTrade).toBe(false)
  })

  it('keeps the snapshot over a tick older than it', async () => {
    act(() => feed.push('NIFTY28OCT2525900CE', 'NFO', { ltp: 128 }, 'websocket', Date.now() - 1))
    const { result } = renderHook(() =>
      useLiveQuote('NIFTY28OCT2525900CE', 'NFO', { mode: 'Quote' })
    )
    await settle(10)
    expect(result.current.data.ltp).toBe(130)
  })
})
