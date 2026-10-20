/**
 * useLivePrice under rule 7 (M6.2, M6.3): prices from the socket, REST only
 * for the first snapshot and re-syncs, and P&L moved from the broker's own
 * figure. Runs the real hook and the real useMarketData over the market-data
 * harness, so a tick arrives the way the shared manager delivers it.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args) },
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
import type { Position } from '@/types/trading'
import { useLivePrice } from './useLivePrice'

const CALL = 'NIFTY28OCT2525900CE'

/** Bought 130 at 120, sold 65 at 140: +1,300 booked, 65 held, ltp 130. */
const PARTLY_BOOKED: Position = {
  symbol: CALL,
  exchange: 'NFO',
  product: 'NRML',
  quantity: 65,
  average_price: 120,
  ltp: 130,
  pnl: 1950,
  pnlpercent: 0,
}

function quotes(ltp: number) {
  return { status: 'success', results: [{ symbol: CALL, exchange: 'NFO', data: { ltp } }] }
}

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  feed.reset()
  getMultiQuotes.mockReset().mockResolvedValue(quotes(130))
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('useLivePrice REST quotes', () => {
  it('reads them once for a symbol set and never on a timer while the socket is healthy', async () => {
    const { rerender } = renderHook(({ rows }) => useLivePrice(rows), {
      initialProps: { rows: [PARTLY_BOOKED] },
    })
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    // Five minutes of a healthy socket: upstream asked every 30 seconds.
    await settle(5 * 60_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    // A re-read book with the same instruments is not a new symbol set.
    rerender({ rows: [{ ...PARTLY_BOOKED, pnl: 2000 }] })
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    rerender({ rows: [PARTLY_BOOKED, { ...PARTLY_BOOKED, symbol: 'NIFTY28OCT2525900PE' }] })
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })

  it('reads them again when the market-data socket signs in again', async () => {
    renderHook(() => useLivePrice([PARTLY_BOOKED]))
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    act(() => feed.setState({ isAuthenticated: false, connectionState: 'connecting' }))
    await settle(5_000)
    act(() =>
      feed.setState({ isAuthenticated: true, connectionState: 'authenticated', connectionEpoch: 2 })
    )
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })

  it('reads them at most once in 5 seconds while a book gains a row with each fill', async () => {
    const row = (i: number) => ({ ...PARTLY_BOOKED, symbol: `NIFTY28OCT25${25900 + i * 50}CE` })
    const { rerender } = renderHook(({ rows }) => useLivePrice(rows), {
      initialProps: { rows: [row(0)] },
    })
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    // Ten fills a quarter of a second apart, each a new instrument in the book.
    const rows = [row(0)]
    for (let i = 1; i <= 10; i++) {
      rows.push(row(i))
      rerender({ rows: [...rows] })
      await settle(250)
    }
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)
    // One read at the end of the gap, for the whole set.
    await settle(5_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
    expect(getMultiQuotes.mock.calls[1][1]).toHaveLength(11)
  })

  it('reads none while the page is hidden, and once when it is seen again', async () => {
    const { rerender } = renderHook(({ rows }) => useLivePrice(rows), {
      initialProps: { rows: [PARTLY_BOOKED] },
    })
    await settle()
    act(() => setVisibility('hidden'))
    await settle(6_000)
    rerender({ rows: [PARTLY_BOOKED, { ...PARTLY_BOOKED, symbol: 'NIFTY28OCT2525900PE' }] })
    await settle(6_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    act(() => setVisibility('visible'))
    await settle(10)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })

  it('reads them again on coming back after more than 15 seconds away, not after a glance away', async () => {
    renderHook(() => useLivePrice([PARTLY_BOOKED]))
    await settle()

    act(() => setVisibility('hidden'))
    await settle(5_000)
    act(() => setVisibility('visible'))
    await settle(10)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    act(() => setVisibility('hidden'))
    await settle(20_000)
    act(() => setVisibility('visible'))
    await settle(10)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })

  it('reads them once, not twice, after a long time away during which the rows changed', async () => {
    const { rerender } = renderHook(({ rows }) => useLivePrice(rows), {
      initialProps: { rows: [PARTLY_BOOKED] },
    })
    await settle()
    act(() => setVisibility('hidden'))
    rerender({ rows: [PARTLY_BOOKED, { ...PARTLY_BOOKED, symbol: 'NIFTY28OCT2525900PE' }] })
    await settle(20_000)
    act(() => setVisibility('visible'))
    await settle(10)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
    await settle(10_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })
})

describe('useLivePrice P&L', () => {
  it('moves a partly booked position from the broker figure, keeping what was booked', async () => {
    const bookAt = Date.now()
    const { result } = renderHook(() => useLivePrice([PARTLY_BOOKED], { snapshotAt: bookAt }))
    await settle(1_000)

    act(() => feed.push(CALL, 'NFO', { ltp: 134 }))
    const row = result.current.data[0]
    expect(row.ltp).toBe(134)
    expect(row.pnl).toBeCloseTo(1950 + 4 * 65)
    expect(row._live).toBe(true)
    expect(row._tick).toBe('up')
    expect(result.current.feed).toMatchObject({ due: 1, connected: true })
    expect(result.current.feed.lastTickAt).not.toBeNull()

    act(() => feed.push(CALL, 'NFO', { ltp: 131 }))
    expect(result.current.data[0].pnl).toBeCloseTo(1950 + 65)
    expect(result.current.data[0]._tick).toBe('down')
  })

  it('leaves the broker figures alone for a tick older than the book from an earlier session of the feed', async () => {
    act(() => feed.push(CALL, 'NFO', { ltp: 125 }, 'websocket', Date.now() - 10_000))
    act(() => feed.setState({ connectionEpoch: 2 }))
    getMultiQuotes.mockResolvedValue({ status: 'success', results: [] })
    const { result } = renderHook(() => useLivePrice([PARTLY_BOOKED], { snapshotAt: Date.now() }))
    await settle()

    expect(result.current.data[0]).toMatchObject({ ltp: 130, pnl: 1950, _dataSource: 'rest' })
  })

  it('keeps the last live tick over a book read since, whose price can trail the feed', async () => {
    act(() => feed.push(CALL, 'NFO', { ltp: 134 }, 'websocket', Date.now() - 300))
    getMultiQuotes.mockResolvedValue({ status: 'success', results: [] })
    const { result } = renderHook(() => useLivePrice([PARTLY_BOOKED], { snapshotAt: Date.now() }))
    await settle()

    expect(result.current.data[0]).toMatchObject({ ltp: 134, _live: true })
    expect(result.current.data[0].pnl).toBeCloseTo(1950 + 4 * 65)
  })

  it('moves an MIS long and an NRML short in one instrument each from its own broker figure', async () => {
    getMultiQuotes.mockResolvedValue({ status: 'success', results: [] })
    const at = Date.now() - 1_000
    const rows = [
      { ...PARTLY_BOOKED, product: 'MIS', quantity: 65, average_price: 120, ltp: 130, pnl: 650 },
      { ...PARTLY_BOOKED, product: 'NRML', quantity: -30, average_price: 140, ltp: 130, pnl: 300 },
    ]
    const { result } = renderHook(() => useLivePrice(rows, { snapshotAt: at }))
    await settle()
    act(() => feed.push(CALL, 'NFO', { ltp: 132 }))

    const [long, short] = result.current.data
    expect(long.pnl).toBeCloseTo(650 + 2 * 65)
    expect(short.pnl).toBeCloseTo(300 - 2 * 30)
  })

  it('without a snapshot time, counts the book from when it was first seen', async () => {
    getMultiQuotes.mockResolvedValue({ status: 'success', results: [] })
    const { result } = renderHook(() => useLivePrice([PARTLY_BOOKED]))
    await settle(1_000)

    act(() => feed.push(CALL, 'NFO', { ltp: 132 }))
    expect(result.current.data[0].pnl).toBeCloseTo(1950 + 2 * 65)
  })
})
