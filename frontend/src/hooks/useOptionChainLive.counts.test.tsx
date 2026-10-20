/**
 * The option chain's REST poll, kept only for what the ticks do not carry
 * (open interest and volume, and PCR from them). While the feed sends neither,
 * the chain is read every 30 seconds as before; once ticks carry both, no read
 * is made on the timer and the chain takes them from the ticks. Each leg's
 * LTP carries the direction of its last tick.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'
import { useOptionChainLive } from './useOptionChainLive'

const market = vi.hoisted(() => ({
  data: new Map<string, SymbolData>(),
  isAuthenticated: true,
  connectionEpoch: 1,
}))

vi.mock('./useMarketData', () => ({
  useMarketData: () => ({
    data: market.data,
    isConnected: true,
    isAuthenticated: market.isAuthenticated,
    isPaused: false,
    connectionEpoch: market.connectionEpoch,
  }),
}))

const CE = 'NIFTY28OCT2525900CE'
const PE = 'NIFTY28OCT2525900PE'

function leg(symbol: string, ltp: number) {
  return {
    symbol,
    label: 'ATM',
    ltp,
    bid: ltp - 0.5,
    ask: ltp + 0.5,
    bid_qty: 75,
    ask_qty: 75,
    open: ltp,
    high: ltp,
    low: ltp,
    prev_close: ltp,
    volume: 1000,
    oi: 5000,
    lotsize: 65,
    tick_size: 0.05,
  }
}

function chain() {
  return {
    status: 'success',
    underlying: 'NIFTY',
    underlying_symbol: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    underlying_ltp: 25900,
    underlying_prev_close: 25850,
    expiry_date: '28OCT25',
    atm_strike: 25900,
    chain: [{ strike: 25900, ce: leg(CE, 120), pe: leg(PE, 110) }],
  }
}

function tick(symbol: string, data: Record<string, number>): SymbolData {
  return {
    symbol,
    exchange: 'NFO',
    data: data as SymbolData['data'],
    lastUpdate: Date.now(),
    updateSource: 'websocket',
    connectionEpoch: 1,
  }
}

const fetchMock = vi.fn()

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  market.data = new Map()
  market.isAuthenticated = true
  market.connectionEpoch = 1
  fetchMock.mockReset().mockImplementation(async () => new Response(JSON.stringify(chain())))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const render = () =>
  renderHook(() =>
    useOptionChainLive('key', 'NIFTY', 'NFO', 'NFO', '28OCT25', 10, {
      enabled: true,
      oiRefreshInterval: 30_000,
    })
  )

describe('useOptionChainLive and the REST poll', () => {
  it('reads the chain every 30 seconds while the ticks carry no open interest or volume', async () => {
    const { rerender } = render()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    market.data = new Map([[`NFO:${CE}`, tick(CE, { ltp: 121 })]])
    rerender()
    await settle(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('reads nothing on the timer once the ticks carry both, and takes them from the ticks', async () => {
    const { result, rerender } = render()
    await settle()

    market.data = new Map([
      [`NFO:${CE}`, tick(CE, { ltp: 121, oi: 5200, volume: 1500 })],
      [`NFO:${PE}`, tick(PE, { ltp: 109, oi: 4800, volume: 1300 })],
    ])
    rerender()
    await settle(90_000)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(result.current.countsFromTicks).toBe(true)
    expect(result.current.data?.chain[0].ce?.oi).toBe(5200)
    expect(result.current.data?.chain[0].pe?.volume).toBe(1300)
  })

  it("marks each leg's LTP with its last tick's direction", async () => {
    const { result, rerender } = render()
    await settle()

    market.data = new Map([
      [`NFO:${CE}`, tick(CE, { ltp: 121 })],
      [`NFO:${PE}`, tick(PE, { ltp: 108 })],
    ])
    rerender()
    await settle()

    expect(result.current.tickDirections.get(`NFO:${CE}`)).toBe('up')
    expect(result.current.tickDirections.get(`NFO:${PE}`)).toBe('down')
  })

  it('reads the chain again, quietly, when the market-data socket signs in again', async () => {
    const { result, rerender } = render()
    await settle()
    let answer: (response: Response) => void = () => {}
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve
        })
    )
    market.connectionEpoch = 2
    rerender()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    // A re-sync nobody asked for: nothing flips to loading or "refreshing".
    expect(result.current.isLoading).toBe(false)

    answer(new Response(JSON.stringify(chain())))
    await settle()
    expect(result.current.isLoading).toBe(false)
  })

  it('re-centres the strikes, quietly and at most every 15 s, when the underlying passes the next strike', async () => {
    const twoStrikes = {
      ...chain(),
      chain: [
        {
          strike: 25850,
          ce: leg('NIFTY28OCT2525850CE', 150),
          pe: leg('NIFTY28OCT2525850PE', 90),
        },
        { strike: 25900, ce: leg(CE, 120), pe: leg(PE, 110) },
      ],
    }
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(twoStrikes)))
    const index = (ltp: number): SymbolData => ({
      ...tick('NIFTY', { ltp }),
      exchange: 'NSE_INDEX',
    })
    const { result, rerender } = render()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Within a strike of the ATM: nothing to re-centre.
    market.data = new Map([['NSE_INDEX:NIFTY', index(25940)]])
    rerender()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Past the next strike: one quiet read.
    market.data = new Map([['NSE_INDEX:NIFTY', index(26000)]])
    rerender()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result.current.isLoading).toBe(false)

    // Still away a few seconds later: not again until 15 s have passed.
    await settle(5_000)
    market.data = new Map([['NSE_INDEX:NIFTY', index(26010)]])
    rerender()
    await settle()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
