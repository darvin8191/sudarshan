/**
 * The index strip, live. The market-data manager is replaced by the shared
 * test harness and everything above it is real, `useMarketData` included, so
 * these pin what the owner sees: levels that move with each tick, a change
 * that stays on the previous close, a stale mark that appears only when ticks
 * are due and missing, and subscriptions that close when the strip goes away.
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args) },
}))

import { feed } from '@/test/marketDataHarness'
import { IndexStrip, SNAPSHOT_MAX_AGE_MS } from './IndexStrip'
import { STALE_MS } from './live'

const MINUS = '−'
const OPEN = () => true
const CLOSED = () => false

function quote(symbol: string, ltp: number, prev_close: number) {
  return {
    symbol,
    exchange: 'NSE_INDEX',
    data: { ltp, prev_close, open: 0, high: 0, low: 0, volume: 0, oi: 0, bid: 0, ask: 0 },
  }
}

const ANSWER = {
  status: 'success',
  results: [
    quote('NIFTY', 25874.3, 25714.85),
    quote('BANKNIFTY', 57912.15, 58016.6),
    quote('INDIAVIX', 11.42, 11.79),
  ],
}

const SUBSCRIPTIONS = ['NSE_INDEX:NIFTY:LTP', 'NSE_INDEX:BANKNIFTY:LTP', 'NSE_INDEX:INDIAVIX:LTP']

/** Let the quote call's promise settle inside React's act. */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })
}

/** Move the fake clock on, running every timer due on the way. */
async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function tick(symbol: string, ltp: number, source: 'websocket' | 'rest' = 'websocket') {
  act(() => feed.push(symbol, 'NSE_INDEX', { ltp }, source))
}

const tiles = () => screen.getAllByRole('listitem')
const line = (container: HTMLElement) => container.querySelector('[data-feed]')

beforeEach(() => {
  feed.reset()
  getMultiQuotes.mockReset()
  getMultiQuotes.mockResolvedValue(ANSWER)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('IndexStrip', () => {
  it('shows the REST read until the first tick, with the change on the previous close', async () => {
    const { container } = render(<IndexStrip apiKey="key" marketOpen={CLOSED} />)
    await settle()

    expect(tiles().map((tile) => tile.textContent)).toEqual([
      'NIFTY 5025,874.30+0.62%',
      `NIFTY BANK57,912.15${MINUS}0.18%`,
      `INDIA VIX11.42${MINUS}3.14%`,
    ])
    expect(tiles()[0].querySelector('[data-direction]')).toHaveAttribute('data-direction', 'up')
    expect(tiles()[1].querySelector('[data-direction]')).toHaveAttribute('data-direction', 'down')
    expect(getMultiQuotes).toHaveBeenCalledWith('key', [
      { symbol: 'NIFTY', exchange: 'NSE_INDEX' },
      { symbol: 'BANKNIFTY', exchange: 'NSE_INDEX' },
      { symbol: 'INDIAVIX', exchange: 'NSE_INDEX' },
    ])
    expect(line(container)).toHaveTextContent('Market closed')
  })

  it('moves with every tick on the shared feed, and keeps the change on the previous close', async () => {
    const { container } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    expect(feed.live()).toEqual(SUBSCRIPTIONS)

    tick('NIFTY', 25900)
    expect(tiles()[0]).toHaveTextContent('NIFTY 5025,900.00+0.72%')
    expect(line(container)).toHaveAttribute('data-feed', 'live')
    expect(line(container)).toHaveTextContent('Live')

    tick('NIFTY', 25650.5)
    expect(tiles()[0]).toHaveTextContent(`NIFTY 5025,650.50${MINUS}0.25%`)
    expect(tiles()[0].querySelector('[data-direction]')).toHaveAttribute('data-direction', 'down')
    // One read for the previous close, then ticks only.
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)
  })

  it('ignores a tick older than the REST read, which is newer', async () => {
    render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()

    act(() => feed.push('NIFTY', 'NSE_INDEX', { ltp: 25000 }, 'websocket', Date.now() - 5000))
    expect(tiles()[0]).toHaveTextContent('25,874.30')
  })

  it('never says "Live" on a REST fallback price, however fresh', async () => {
    const { container } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()

    tick('NIFTY', 25901, 'rest')
    expect(tiles()[0]).toHaveTextContent('25,901.00')
    expect(line(container)).toHaveAttribute('data-feed', 'delayed')
    expect(line(container)).toHaveTextContent(/^Updated \ds ago$/)
  })

  it('mutes a level and marks it with a clock once NSE is open and nothing has come for ten seconds', async () => {
    vi.useFakeTimers()
    const { container } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    tick('NIFTY', 25900)
    expect(container.querySelectorAll('[data-stale]')).toHaveLength(0)

    await pass(STALE_MS + 1000)
    expect(container.querySelectorAll('[data-stale]')).toHaveLength(3)
    expect(screen.getAllByText('Stale')).toHaveLength(3)
    expect(tiles()[0].querySelector('.sd-display')).toHaveClass('text-muted-foreground')
    expect(line(container)).toHaveAttribute('data-feed', 'stale')
    expect(line(container)).toHaveTextContent('Stale · updated 11s ago')

    tick('NIFTY', 25901)
    expect(tiles()[0]).not.toHaveAttribute('data-stale')
    expect(line(container)).toHaveTextContent('Live')
  })

  it('leaves a quiet index unmarked while the others tick, and marks every one when the feed stops', async () => {
    vi.useFakeTimers()
    const { container } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    tick('INDIAVIX', 11.4)
    for (let second = 0; second < 20; second += 2) {
      tick('NIFTY', 25900 + second)
      tick('BANKNIFTY', 57900 + second)
      await pass(2000)
    }
    expect(container.querySelectorAll('[data-stale]')).toHaveLength(0)
    expect(line(container)).toHaveTextContent('Live')

    await pass(STALE_MS + 1000)
    expect(container.querySelectorAll('[data-stale]')).toHaveLength(3)
  })

  it('says it is reconnecting while the feed is down, and is Live again with the next tick', async () => {
    const { container } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    tick('NIFTY', 25900)
    expect(line(container)).toHaveTextContent('Live')

    act(() =>
      feed.setState({ connectionState: 'connecting', isConnected: false, isAuthenticated: false })
    )
    expect(line(container)).toHaveAttribute('data-feed', 'connecting')
    expect(line(container)).toHaveTextContent(/^Reconnecting · updated \ds ago$/)

    act(() =>
      feed.setState({ connectionState: 'authenticated', isConnected: true, isAuthenticated: true })
    )
    tick('NIFTY', 25901)
    expect(line(container)).toHaveTextContent('Live')
  })

  it('says it is connecting before the first tick, not reconnecting', async () => {
    feed.setState({ connectionState: 'connecting', isConnected: false, isAuthenticated: false })
    const { container } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    expect(line(container)).toHaveTextContent(/^Connecting$/)
  })

  it('marks nothing stale while the market is closed, and says it is closed', async () => {
    vi.useFakeTimers()
    const { container } = render(<IndexStrip apiKey="key" marketOpen={CLOSED} />)
    await settle()

    await pass(60_000)
    expect(container.querySelectorAll('[data-stale]')).toHaveLength(0)
    expect(line(container)).toHaveTextContent('Market closed')
  })

  it('closes every subscription when it goes away', async () => {
    const { unmount } = render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    expect(feed.live()).toEqual(SUBSCRIPTIONS)

    unmount()
    expect(feed.live()).toEqual([])
    expect(feed.closed.sort()).toEqual([...SUBSCRIPTIONS].sort())
  })

  it('asks nothing, opens nothing and shows nothing without an API key', async () => {
    const { container } = render(<IndexStrip apiKey={null} marketOpen={OPEN} />)
    await settle()

    expect(container).toBeEmptyDOMElement()
    expect(getMultiQuotes).not.toHaveBeenCalled()
    expect(feed.opened).toEqual([])
  })

  it('shows no change rather than a wrong one when the previous close is missing', async () => {
    getMultiQuotes.mockResolvedValue({ status: 'success', results: [quote('NIFTY', 25874.3, 0)] })
    render(<IndexStrip apiKey="key" marketOpen={CLOSED} />)
    await settle()

    const [nifty, bank] = tiles()
    expect(nifty).toHaveTextContent(/^NIFTY 5025,874\.30$/)
    expect(bank).toHaveTextContent('Not available')
  })

  it('says so when the quotes cannot be read and nothing has ticked', async () => {
    getMultiQuotes.mockRejectedValue(new Error('network'))
    render(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()

    expect(screen.getByText('Index prices are not available right now.')).toBeInTheDocument()
    expect(screen.getAllByText('Not available')).toHaveLength(3)

    tick('NIFTY', 25900)
    expect(screen.queryByText('Index prices are not available right now.')).toBeNull()
    expect(tiles()[0]).toHaveTextContent(/^NIFTY 5025,900\.00$/)
  })

  it('never reads on a timer, only when NSE opens or closes and on a reconnect after ten minutes', async () => {
    vi.useFakeTimers()
    const { rerender } = render(<IndexStrip apiKey="key" marketOpen={CLOSED} />)
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    await pass(5 * 60_000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    // A reconnect soon after a read changes nothing.
    act(() => feed.setState({ connectionEpoch: 2 }))
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    // The open: the previous close moves on.
    rerender(<IndexStrip apiKey="key" marketOpen={OPEN} />)
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)

    await pass(SNAPSHOT_MAX_AGE_MS + 1000)
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
    act(() => feed.setState({ connectionEpoch: 3 }))
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(3)
  })
})
