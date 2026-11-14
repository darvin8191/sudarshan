/**
 * SUDARSHAN D9 (screens design 2.4, M3.7): the dashboard back from sleep and across midnight, as
 * one page. While the phone sleeps nothing is read; back after more than 15 s the day facts and
 * the broker's figures are read again, every live line says Reconnecting over the old figures
 * until a tick comes, and only then Real-time. A page open past midnight in India asks for the new
 * day's facts. (frontend/e2e/sd-dashboard.spec.ts runs the same in a browser at 360 x 800.)
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const getPositions = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: vi.fn().mockResolvedValue({ status: 'success', results: [] }),
    getHoldings: vi.fn().mockResolvedValue({ status: 'success', data: { holdings: [] } }),
    getPositions: (...args: unknown[]) => getPositions(...args),
  },
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))
const calendar = {
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))

import { useAuthStore } from '@/stores/authStore'
import { at, serverFacts } from '@/test/dayFacts'
import { feed } from '@/test/marketDataHarness'
import { render, screen } from '@/test/test-utils'
import Dashboard from './Dashboard'

const CALL = 'NIFTY17NOV2625900CE'

/** Every request the page makes, the day facts answered for the day each read asks on. */
function server() {
  const calls: string[] = []
  getPositions.mockImplementation(async () => {
    calls.push('positionbook')
    return {
      status: 'success',
      data: [
        {
          symbol: CALL,
          exchange: 'NFO',
          product: 'NRML',
          quantity: 65,
          average_price: 120,
          ltp: 130,
          pnl: 650,
          pnlpercent: 8.3,
        },
      ],
    }
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
      if (url === '/auth/dashboard-data') {
        return ok({
          status: 'success',
          data: {
            availablecash: '1000',
            collateral: '0',
            m2munrealized: '650',
            m2mrealized: '0',
            utiliseddebits: '0',
          },
        })
      }
      if (url === '/api/master-contract/status') return ok({ status: 'success' })
      if (url === '/sudarshan/session/today') {
        const day = new Date(Date.now() + 5.5 * 3_600_000).toISOString().slice(0, 10)
        return ok({ status: 'success', data: serverFacts(day) })
      }
      return { ok: false, status: 404, json: async () => ({}) }
    })
  )
  return calls
}

const count = (calls: string[], url: string) => calls.filter((each) => each === url).length

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  feed.reset()
  setVisibility('visible')
  useAuthStore.setState({ apiKey: 'key', sessionChecked: true, brokerSessionExpired: false })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  setVisibility('visible')
  useAuthStore.setState({ apiKey: null, sessionChecked: false })
})

describe('the dashboard back from sleep', () => {
  it('reads nothing while asleep, then the facts and the figures again, Reconnecting until a tick', async () => {
    vi.useFakeTimers({ now: at('2026-11-17', '11:00') })
    const calls = server()
    render(<Dashboard />)
    await pass(0)
    act(() => feed.push(CALL, 'NFO', { ltp: 131 }))
    await pass(0)
    expect(document.querySelector('[data-feed="live"]')).not.toBeNull()
    const facts = count(calls, '/sudarshan/session/today')
    const funds = count(calls, '/auth/dashboard-data')

    // Asleep for 12 minutes: the shared socket pauses and nothing is read.
    setVisibility('hidden')
    act(() => feed.setState({ connectionState: 'paused', isConnected: false, isPaused: true }))
    await pass(12 * 60_000)
    expect(count(calls, '/sudarshan/session/today')).toBe(facts)
    expect(count(calls, '/auth/dashboard-data')).toBe(funds)

    // Awake: the first frame says Reconnecting over the old figures, never Real-time.
    act(() =>
      feed.setState({
        connectionState: 'connecting',
        isConnected: false,
        isAuthenticated: false,
        isPaused: false,
      })
    )
    setVisibility('visible')
    await pass(0)
    expect(document.querySelector('[data-feed="live"]')).toBeNull()
    expect(screen.getAllByText(/^Reconnecting · updated/).length).toBeGreaterThan(0)
    expect(count(calls, '/sudarshan/session/today')).toBe(facts + 1)
    expect(count(calls, '/auth/dashboard-data')).toBe(funds + 1)

    // The socket signs in again and the next tick brings Real-time back.
    act(() =>
      feed.setState({
        connectionState: 'authenticated',
        isConnected: true,
        isAuthenticated: true,
        connectionEpoch: 2,
      })
    )
    act(() => feed.push(CALL, 'NFO', { ltp: 132 }))
    await pass(0)
    expect(document.querySelector('[data-feed="live"]')).not.toBeNull()
  })

  it("asks for the new day's facts when the page is open past midnight in India", async () => {
    vi.useFakeTimers({ now: at('2026-11-16', '23:58') })
    const calls = server()
    render(<Dashboard />)
    await pass(0)
    expect(screen.getByText(/Expiry tomorrow: NIFTY/)).toBeVisible()
    const facts = count(calls, '/sudarshan/session/today')

    await pass(3 * 60_000) // 00:01 on the 17th, NIFTY's expiry day
    await pass(0)
    expect(count(calls, '/sudarshan/session/today')).toBe(facts + 1)
    expect(screen.getByText('Expiry today: NIFTY · stops 15:40')).toBeVisible()
  })
})
