/**
 * The D home, wired to the data the page loads: the funds summary
 * (/auth/dashboard-data), the position book that lets the P&L move tick by
 * tick between reads, and the master contract state. These pin where every
 * figure comes from, when the broker is read again, and the ways it can fail.
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

/** The app-wide Socket.IO connection, reduced to what the order-event hook uses. */
const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return this
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
      return this
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    listeners: (event: string) => handlers.get(event)?.size ?? 0,
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

let marketOpen = false
/** One object, as the real hook keeps its answers stable between renders. */
const calendar = {
  isMarketOpen: () => marketOpen,
  isAnyMarketOpen: () => marketOpen,
  isLoading: false,
  error: null,
}
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))

import { useAuthStore } from '@/stores/authStore'
import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { feed } from '@/test/marketDataHarness'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import Dashboard from './Dashboard'

const FUNDS = {
  availablecash: '48320.50',
  collateral: '0.00',
  m2munrealized: '650.75',
  m2mrealized: '1800.00',
  utiliseddebits: '12400.00',
}

const CALL = 'NIFTY28OCT2525900CE'
const BOOK = [
  {
    symbol: CALL,
    exchange: 'NFO',
    product: 'NRML',
    quantity: 65,
    average_price: 120,
    ltp: 130,
    pnl: 650.75,
    pnlpercent: 8.34,
  },
]

type Answer = { status: number; body: unknown }

/** How long the funds call takes to answer, in fake-clock milliseconds. */
let latency = 0

/** Every request in the order it was made: the funds, the book and the contract state. */
function server(...answers: Answer[]) {
  const calls: string[] = []
  let next = 0
  getPositions.mockImplementation(async () => {
    calls.push('positionbook')
    return { status: 'success', data: BOOK }
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      if (url === '/auth/dashboard-data') {
        const funds = answers[Math.min(next, answers.length - 1)]
        next += 1
        if (latency > 0) await new Promise((resolve) => setTimeout(resolve, latency))
        return { ok: funds.status === 200, status: funds.status, json: async () => funds.body }
      }
      if (url === '/api/master-contract/status') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: 'success', total_symbols: 118432 }),
        }
      }
      return { ok: false, status: 404, json: async () => ({}) }
    })
  )
  return calls
}

const OK = { status: 200, body: { status: 'success', data: FUNDS } }
const reads = (calls: string[]) => calls.filter((url) => url === '/auth/dashboard-data').length

/** Run whatever is due on the fake clock, and the promises it settles. */
async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  marketOpen = false
  latency = 0
  feed.reset()
  getPositions.mockReset()
  useAuthStore.setState({ apiKey: 'key' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  useAuthStore.setState({ apiKey: null })
})

describe('Dashboard', () => {
  it("shows today's P&L, the funds and the master contract state from the server", async () => {
    server(OK)
    render(<Dashboard />)

    expect(await screen.findByTestId('pnl-total')).toHaveTextContent('+₹2,450.75')
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByText('₹48,320.50')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /Master contract ready/ })).toHaveAttribute(
      'href',
      '/master-contract'
    )
    expect(screen.getByRole('region', { name: 'Indices' })).toBeInTheDocument()
  })

  it('reads the position book before the funds, then moves the P&L with each tick', async () => {
    const calls = server(OK)
    render(<Dashboard />)

    expect(await screen.findByText('1 open position')).toBeInTheDocument()
    const book = calls.indexOf('positionbook')
    expect(book).toBeGreaterThanOrEqual(0)
    expect(book).toBeLessThan(calls.indexOf('/auth/dashboard-data'))
    expect(getPositions).toHaveBeenCalledWith('key')

    // The P&L card subscribes in an effect, which can run after the text above
    // is on the page: a tick pushed before it reaches no one. Wait for it.
    await waitFor(() => expect(feed.live()).toContain(`NFO:${CALL}:LTP`))
    act(() => feed.push(CALL, 'NFO', { ltp: 134 }))
    expect(screen.getByTestId('pnl-total')).toHaveTextContent('+₹2,710.75')
  })

  it('reads the broker again every 45 seconds while a market is open, never while all are shut', async () => {
    vi.useFakeTimers()
    const calls = server(OK)
    render(<Dashboard />)
    await pass(0)
    expect(reads(calls)).toBe(1)

    await pass(RESYNC_MS * 2)
    expect(reads(calls)).toBe(1)

    marketOpen = true
    await pass(RESYNC_MS)
    expect(reads(calls)).toBe(2)
    expect(calls.filter((url) => url === 'positionbook')).toHaveLength(2)
  })

  it('reads the broker again when it reports a fill, a rejection or a square-off', async () => {
    vi.useFakeTimers()
    const calls = server(OK)
    render(<Dashboard />)
    await pass(0)
    expect(reads(calls)).toBe(1)
    // Two listeners: the P&L's, and the holdings card's (which answers only
    // an update that can change the holdings).
    expect(socket.listeners('order_update')).toBe(2)

    act(() => socket.fire('order_update', { orderid: '251020000598204', status: 'complete' }))
    await pass(600)
    expect(reads(calls)).toBe(2)
    expect(calls.filter((url) => url === 'positionbook')).toHaveLength(2)
  })

  it('answers a burst of order updates with one read, and one more once the broker has caught up', async () => {
    vi.useFakeTimers()
    latency = 300
    const calls = server(OK)
    render(<Dashboard />)
    await pass(400)
    expect(reads(calls)).toBe(1)

    // One order passing through four states inside a second.
    for (const status of ['open pending', 'open', 'trigger pending', 'complete']) {
      act(() => socket.fire('order_update', { status }))
      await pass(100)
    }
    await pass(2000)
    // One read for the burst, not one per update...
    expect(reads(calls)).toBe(2)
    // ...and one more when Upstox's books have caught up with its push.
    await pass(2000)
    expect(reads(calls)).toBe(3)
    await pass(30_000)
    expect(reads(calls)).toBe(3)
  })

  it('reads nothing while the page is hidden, and once when it is seen again', async () => {
    vi.useFakeTimers()
    marketOpen = true
    const calls = server(OK)
    render(<Dashboard />)
    await pass(0)
    expect(reads(calls)).toBe(1)

    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    act(() => socket.fire('order_update', { orderid: '1', order_status: 'complete' }))
    await pass(RESYNC_MS)
    expect(reads(calls)).toBe(1)

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await pass(0)
    expect(reads(calls)).toBe(2)
  })

  it('keeps the last figures and says so when a later read fails', async () => {
    vi.useFakeTimers()
    marketOpen = true
    server(OK, { status: 200, body: { status: 'error', message: 'Failed to get funds' } })
    render(<Dashboard />)
    await pass(0)
    expect(screen.getByTestId('pnl-total')).toHaveTextContent('+₹2,450.75')

    await pass(RESYNC_MS)
    expect(screen.getByTestId('pnl-total')).toHaveTextContent('+₹2,450.75')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('Could not refresh just now.')
  })

  it('sends an expired broker session to the reconnect screen, not to login', async () => {
    server({ status: 401, body: { status: 'error', code: 'BROKER_SESSION_EXPIRED' } })
    render(<Dashboard />)

    expect(await screen.findByRole('link', { name: 'Reconnect Broker' })).toHaveAttribute(
      'href',
      '/broker'
    )
  })

  it('says what failed and offers to try again when the funds cannot be loaded', async () => {
    const calls = server({ status: 200, body: { status: 'error', message: 'Failed to get funds' } })
    const user = userEvent.setup()
    render(<Dashboard />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Funds and P&L could not be loaded')
    expect(alert).toHaveTextContent('Failed to get funds')

    const before = reads(calls)
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(reads(calls)).toBe(before + 1))
  })
})
