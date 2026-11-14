/**
 * The chain hook on the shared market-data manager's test harness: real subscriptions, ticks pushed
 * by hand, frames run by hand, the bridge's reads answered by the test.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  readChain: vi.fn(),
  readFallback: vi.fn(),
}))

import { frames, manualClock } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { type ChainAnswer, readChain, readFallback } from './api'
import { chainOf, EXPIRY, LISTING, NOW, symbolOf } from './fixtures'
import { AWAY_MS, MIN_GAP_MS, RESYNC_MS, useChainModel } from './useChainModel'
import { FOLLOW_CAP, RECENTRE_MIN_MS } from './window'

const chainRead = vi.mocked(readChain)
const fallbackRead = vi.mocked(readFallback)
const ok = (chain = chainOf()): ChainAnswer => ({ state: 'ok', words: null, chain })

interface Props {
  expiry: string | null
  marketOpen: boolean | null
  sheet?: string[]
  enabled?: boolean
}

async function mount(props: Partial<Props> = {}) {
  const view = renderHook(
    (p: Props) => useChainModel({ listing: LISTING, apiKey: 'test-key', now: Date.now(), ...p }),
    { initialProps: { expiry: EXPIRY, marketOpen: true, ...props } as Props }
  )
  await settle()
  return view
}

/** Let the read in flight land, then draw one frame. */
async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
  act(() => frames.flush())
}

const tick = (symbol: string, exchange: string, data: Record<string, number>) =>
  act(() => feed.push(symbol, exchange, data, 'websocket', Date.now()))

function hide(hidden: boolean) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (hidden ? 'hidden' : 'visible'),
  })
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}

beforeEach(() => {
  feed.reset()
  frames.install()
  manualClock()
  vi.setSystemTime(NOW + 1000)
  chainRead.mockReset()
  fallbackRead.mockReset()
  chainRead.mockResolvedValue(ok())
  fallbackRead.mockResolvedValue(null)
})

afterEach(() => {
  hide(false)
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the chain hook', () => {
  it('reads the chain once and follows 84 instruments: 41 strikes, the spot in LTP, the future', async () => {
    const { result, unmount } = await mount()
    expect(chainRead).toHaveBeenCalledTimes(1)
    expect(chainRead.mock.calls[0].slice(0, 3)).toEqual(['NFO', 'NIFTY', EXPIRY])
    expect(result.current.status).toEqual({ state: 'ok', words: null, source: 'upstox' })
    expect(result.current.model?.atm).toBe(25_900)
    const live = feed.live()
    expect(live).toHaveLength(84)
    expect(feed.closed).toEqual([]) // the first window is the right one: nothing is let go
    expect(live.filter((key) => key.endsWith(':LTP'))).toEqual(['NSE_INDEX:NIFTY:LTP'])
    expect(live).toContain('NFO:NIFTY24NOV26FUT:Quote')
    expect(live).toContain(`NFO:${symbolOf(24_900, 'CE')}:Quote`)
    expect(live).toContain(`NFO:${symbolOf(26_900, 'PE')}:Quote`)
    expect(live).not.toContain(`NFO:${symbolOf(24_850, 'CE')}:Quote`)
    expect(result.current.model?.wingsAsOf).toBe(NOW)
    unmount()
    expect(feed.live()).toEqual([])
  })

  it('never follows more than the cap, whatever the sheet adds', async () => {
    const sheet = Array.from({ length: 40 }, (_, i) => `NFO:OTHER${i}`)
    await mount({ sheet })
    expect(feed.live().length).toBeLessThanOrEqual(FOLLOW_CAP)
    expect(feed.live()).toContain('NFO:OTHER39:Quote')
  })

  it('moves a leg on its tick within one frame: price, open interest and its change', async () => {
    const { result } = await mount()
    const before = result.current.model?.rows.find((row) => row.strike === 25_900)?.ce
    tick(symbolOf(25_900, 'CE'), 'NFO', { ltp: 150, oi: (before?.oi ?? 0) + 650, close: 138 })
    expect(result.current.model?.rows.find((row) => row.strike === 25_900)?.ce?.ltp).toBe(
      before?.ltp
    )
    act(() => frames.flush())
    const after = result.current.model?.rows.find((row) => row.strike === 25_900)?.ce
    expect(after?.ltp).toBe(150)
    expect(after?.oiFrom).toBe('tick')
    expect(after?.oiChange).toBe((before?.oiChange ?? 0) + 650)
    expect(after?.buildUp).toBe('long_buildup')
    expect(result.current.line).toEqual({ kind: 'live' })
    expect(chainRead).toHaveBeenCalledTimes(1)
  })

  it('shows the spot, its change and the basis from ticks', async () => {
    const { result } = await mount()
    tick('NIFTY', 'NSE_INDEX', { ltp: 25_920, prev_close: 25_800 })
    tick('NIFTY24NOV26FUT', 'NFO', { ltp: 25_968 })
    act(() => frames.flush())
    expect(result.current.spot).toEqual({
      ltp: 25_920,
      change: 120,
      changePct: (120 / 25_800) * 100,
    })
    expect(result.current.future).toEqual({ ltp: 25_968, basis: 48 })
  })

  it('re-centres once when the money has walked, by difference, and reads the snapshot again', async () => {
    const { result } = await mount()
    const opened = feed.opened.length
    tick('NIFTY', 'NSE_INDEX', { ltp: 25_960 }) // one strike: the window stays
    act(() => frames.flush())
    expect(feed.opened).toHaveLength(opened)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECENTRE_MIN_MS)
    })
    tick('NIFTY', 'NSE_INDEX', { ltp: 26_060 }) // three strikes
    await settle()
    expect(result.current.model?.atm).toBe(26_050)
    expect(feed.opened).toHaveLength(opened + 6)
    expect(feed.closed).toHaveLength(6)
    expect(feed.live()).toHaveLength(84)
    expect(feed.live()).toContain(`NFO:${symbolOf(27_050, 'CE')}:Quote`)
    expect(chainRead).toHaveBeenCalledTimes(2)
    tick('NIFTY', 'NSE_INDEX', { ltp: 26_210 }) // walked again at once: not inside thirty seconds
    await settle()
    expect(feed.closed).toHaveLength(6)
  })

  it('reads again on a reconnect, when the market turns, on return after 15 s, and every 60 s while open', async () => {
    const { rerender } = await mount()
    const reads = () => chainRead.mock.calls.length
    act(() => feed.setState({ connectionEpoch: 2 }))
    await settle()
    expect(reads()).toBe(1) // inside five seconds of the first read
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MIN_GAP_MS)
    })
    act(() => feed.setState({ connectionEpoch: 3 }))
    await settle()
    expect(reads()).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RESYNC_MS)
    })
    expect(reads()).toBe(3)
    hide(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RESYNC_MS * 3)
    })
    expect(reads()).toBe(3) // nothing is read while the page is hidden
    hide(false)
    await settle()
    expect(reads()).toBe(4)
    hide(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AWAY_MS - 1000)
    })
    hide(false)
    await settle()
    expect(reads()).toBe(4) // a short absence reads nothing
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MIN_GAP_MS)
    })
    rerender({ expiry: EXPIRY, marketOpen: false })
    await settle()
    expect(reads()).toBe(5)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RESYNC_MS * 3)
    })
    expect(reads()).toBe(5) // no re-sync timer while the market is closed
  })

  it('reads upstream’s chain once when the first read fails, and says OI change is not there', async () => {
    const fallback = chainOf({ count: 41, from: 24_900 })
    for (const row of fallback.strikes) {
      delete row.ce?.prev_oi
      delete row.pe?.prev_oi
    }
    chainRead.mockResolvedValue({
      state: 'unavailable',
      words: 'The option chain could not be read.',
      chain: null,
    })
    fallbackRead.mockResolvedValue(fallback)
    const { result } = await mount()
    expect(fallbackRead).toHaveBeenCalledTimes(1)
    expect(result.current.status).toEqual({
      state: 'unavailable',
      words: 'The option chain could not be read.',
      source: 'fallback',
    })
    const atm = result.current.model?.rows.find((row) => row.atm)
    expect(atm?.ce?.oi).toBeGreaterThan(0)
    expect(atm?.ce?.oiChange).toBeNull()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MIN_GAP_MS)
    })
    act(() => result.current.refresh())
    await settle()
    expect(fallbackRead).toHaveBeenCalledTimes(1)
    chainRead.mockResolvedValue(ok())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MIN_GAP_MS)
    })
    act(() => result.current.refresh())
    await settle()
    expect(result.current.status.source).toBe('upstox')
    expect(result.current.model?.rows.find((row) => row.atm)?.ce?.oiChange).not.toBeNull()
  })

  it('keeps the chain it holds when a later read fails, with the server’s sentence', async () => {
    const { result } = await mount()
    chainRead.mockResolvedValue({ state: 'wait', words: 'Upstox asked us to wait.', chain: null })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MIN_GAP_MS)
    })
    act(() => result.current.refresh())
    await settle()
    expect(result.current.status).toEqual({
      state: 'wait',
      words: 'Upstox asked us to wait.',
      source: 'upstox',
    })
    expect(result.current.model?.rows).toHaveLength(61)
    expect(fallbackRead).not.toHaveBeenCalled()
  })

  it('holds nothing when today’s Upstox login is needed, and follows only the spot and the future', async () => {
    chainRead.mockResolvedValue({
      state: 'signin',
      words: "Today's Upstox login is needed.",
      chain: null,
    })
    const { result } = await mount()
    expect(result.current.model).toBeNull()
    expect(result.current.status.state).toBe('signin')
    expect(fallbackRead).not.toHaveBeenCalled()
    expect(feed.live()).toHaveLength(2)
  })

  it('reads the other expiry when it is chosen and lets the first one’s legs go', async () => {
    const { result, rerender } = await mount()
    const next = { ...chainOf(), expiry: '2026-11-24' }
    for (const row of next.strikes) {
      if (row.ce) row.ce.symbol = row.ce.symbol.replace('17NOV', '24NOV')
      if (row.pe) row.pe.symbol = row.pe.symbol.replace('17NOV', '24NOV')
    }
    chainRead.mockResolvedValue(ok(next))
    rerender({ expiry: '2026-11-24', marketOpen: true })
    expect(result.current.model).toBeNull()
    expect(result.current.status.state).toBe('reading')
    await settle()
    expect(chainRead).toHaveBeenLastCalledWith('NFO', 'NIFTY', '2026-11-24', expect.anything())
    expect(feed.live().some((key) => key.includes('17NOV26'))).toBe(false)
    expect(feed.live()).toHaveLength(84)
  })

  it('says the contracts have stopped once the expiry’s stop has passed', async () => {
    vi.setSystemTime(LISTING.expiries[0].stops_at + 1000)
    const { result } = await mount()
    expect(result.current.line).toEqual({ kind: 'closed', stoppedAt: LISTING.expiries[0].stops_at })
    expect(result.current.model?.years).toBe(0)
    expect(result.current.model?.rows.find((row) => row.atm)?.ce?.greeks).toBeNull()
  })

  it('reads and follows nothing while it is not enabled', async () => {
    await mount({ enabled: false })
    expect(chainRead).not.toHaveBeenCalled()
    expect(feed.live()).toEqual([])
  })
})
