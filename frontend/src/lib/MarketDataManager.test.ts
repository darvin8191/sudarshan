import { afterEach, describe, expect, it, vi } from 'vitest'
import { MarketDataManager } from './MarketDataManager'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

interface ManagerHarness {
  fallbackMode: boolean
  apiKey: string | null
  enableFallbackMode: () => Promise<void>
  fetchMarketDataViaRest: () => Promise<void>
  disableFallbackMode: () => void
  startFallbackPolling: () => void
  handleMessage: (event: MessageEvent) => void
}

describe('MarketDataManager fallback sequencing', () => {
  afterEach(() => {
    MarketDataManager.resetInstance()
    vi.unstubAllGlobals()
  })

  it('does not let an old REST fallback session replace a newer WebSocket tick', async () => {
    const response = deferred<Response>()
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/v1/multiquotes') return response.promise
        if (url === '/auth/csrf-token') {
          return Promise.resolve(
            new Response(JSON.stringify({ csrf_token: 'csrf' }), { status: 200 })
          )
        }
        if (url === '/api/websocket/apikey') {
          return Promise.resolve(
            new Response(JSON.stringify({ status: 'success', api_key: 'key' }), { status: 200 })
          )
        }
        return Promise.reject(new Error(`Unexpected fetch: ${url}`))
      })
    )

    const manager = MarketDataManager.getInstance()
    const received: number[] = []
    manager.subscribe('NIFTY13AUG2624600CE', 'NFO', 'Depth', (update) => {
      if (update.data.ltp !== undefined) received.push(update.data.ltp)
    })

    const harness = manager as unknown as ManagerHarness
    harness.fallbackMode = true
    harness.apiKey = 'key'
    const pendingFallback = harness.fetchMarketDataViaRest()

    harness.handleMessage(
      new MessageEvent('message', {
        data: JSON.stringify({
          type: 'market_data',
          symbol: 'NIFTY13AUG2624600CE',
          exchange: 'NFO',
          data: { ltp: 200 },
        }),
      })
    )
    harness.disableFallbackMode()
    const startFallbackPolling = vi
      .spyOn(harness, 'startFallbackPolling')
      .mockImplementation(() => {})
    await harness.enableFallbackMode()
    expect(startFallbackPolling).toHaveBeenCalledOnce()
    response.resolve(
      new Response(
        JSON.stringify({
          status: 'success',
          results: [
            {
              symbol: 'NIFTY13AUG2624600CE',
              exchange: 'NFO',
              data: { ltp: 100 },
            },
          ],
        })
      )
    )
    await pendingFallback

    expect(received).toEqual([200])
    expect(manager.getCachedData('NIFTY13AUG2624600CE', 'NFO')).toMatchObject({
      data: { ltp: 200 },
      updateSource: 'websocket',
    })
  })
})

// SUDARSHAN (M11.3): the normalized tick reaches consumers whole.
describe('MarketDataManager keeps the whole tick', () => {
  afterEach(() => {
    MarketDataManager.resetInstance()
  })

  function tick(manager: MarketDataManager, data: Record<string, unknown>): void {
    const harness = manager as unknown as ManagerHarness
    harness.handleMessage(
      new MessageEvent('message', {
        data: JSON.stringify({
          type: 'market_data',
          symbol: 'NIFTY27OCT2625100CE',
          exchange: 'NFO',
          mode: 2,
          data,
        }),
      })
    )
  }

  it('keeps every numeric field and the greeks of a quote tick', () => {
    const manager = MarketDataManager.getInstance()
    manager.subscribe('NIFTY27OCT2625100CE', 'NFO', 'Quote', () => {})

    tick(manager, {
      symbol: 'NIFTY27OCT2625100CE',
      exchange: 'NFO',
      token: 'NSE_FO|12345',
      ltp: 101.5,
      open: 90,
      high: 110,
      low: 88,
      close: 95.25,
      prev_close: 95.25,
      volume: 123450,
      last_trade_quantity: 75,
      last_trade_time: 1760945399000,
      average_price: 99.87,
      total_buy_quantity: 50000,
      total_sell_quantity: 60000,
      oi: 4567800,
      iv: 0.1432,
      greeks: { delta: 0.52, gamma: 0.0011, theta: -12.3, vega: 9.8, rho: 0 },
      timestamp: 1760945400123,
      cas_eligible: true,
    })

    const cached = manager.getCachedData('NIFTY27OCT2625100CE', 'NFO')?.data
    expect(cached).toEqual({
      ltp: 101.5,
      open: 90,
      high: 110,
      low: 88,
      close: 95.25,
      volume: 123450,
      change: undefined,
      change_percent: undefined,
      timestamp: 1760945400123,
      bid_price: undefined,
      ask_price: undefined,
      bid_size: undefined,
      ask_size: undefined,
      depth: undefined,
      prev_close: 95.25,
      last_trade_quantity: 75,
      last_trade_time: 1760945399000,
      average_price: 99.87,
      total_buy_quantity: 50000,
      total_sell_quantity: 60000,
      oi: 4567800,
      iv: 0.1432,
      greeks: { delta: 0.52, gamma: 0.0011, theta: -12.3, vega: 9.8, rho: 0 },
    })
  })

  it('keeps the last value of a field a later tick leaves out', () => {
    const manager = MarketDataManager.getInstance()
    manager.subscribe('NIFTY27OCT2625100CE', 'NFO', 'Quote', () => {})

    tick(manager, { ltp: 101.5, oi: 4567800, prev_close: 95.25, greeks: { delta: 0.52 } })
    tick(manager, { ltp: 102 })

    expect(manager.getCachedData('NIFTY27OCT2625100CE', 'NFO')?.data).toMatchObject({
      ltp: 102,
      oi: 4567800,
      prev_close: 95.25,
      greeks: { delta: 0.52, gamma: null, theta: null, vega: null, rho: null },
    })
  })

  it('keeps a greek the broker could not compute as unknown, never as 0', () => {
    const manager = MarketDataManager.getInstance()
    manager.subscribe('NIFTY27OCT2625100CE', 'NFO', 'Quote', () => {})

    tick(manager, {
      ltp: 0.05,
      greeks: { delta: null, gamma: null, theta: null, vega: null, rho: 0 },
    })

    expect(manager.getCachedData('NIFTY27OCT2625100CE', 'NFO')?.data.greeks).toEqual({
      delta: null,
      gamma: null,
      theta: null,
      vega: null,
      rho: 0,
    })
  })

  it('ignores greeks that carry nothing', () => {
    const manager = MarketDataManager.getInstance()
    manager.subscribe('NIFTY27OCT2625100CE', 'NFO', 'Quote', () => {})

    tick(manager, { ltp: 101.5, greeks: {} })
    tick(manager, { ltp: 101.5, greeks: 'n/a' })

    expect(manager.getCachedData('NIFTY27OCT2625100CE', 'NFO')?.data.greeks).toBeUndefined()
  })
})
