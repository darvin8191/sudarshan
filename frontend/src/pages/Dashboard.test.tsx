/**
 * The D home, wired to the data the page already loads: the funds summary
 * (/auth/dashboard-data) and the master contract state. Every figure on it
 * comes from the server; these pin that, and the two ways it can fail.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/authStore'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import Dashboard from './Dashboard'

vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: vi.fn().mockResolvedValue({ status: 'success', results: [] }),
    getHoldings: vi.fn().mockResolvedValue({ status: 'success', data: { holdings: [] } }),
  },
}))
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({ isMarketOpen: () => false }),
}))

const FUNDS = {
  availablecash: '48320.50',
  collateral: '0.00',
  m2munrealized: '650.75',
  m2mrealized: '1800.00',
  utiliseddebits: '12400.00',
}

function server(funds: { status: number; body: unknown }) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      if (url === '/auth/dashboard-data') {
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

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'key' })
})

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ apiKey: null })
})

describe('Dashboard', () => {
  it("shows today's P&L, the funds and the master contract state from the server", async () => {
    server({ status: 200, body: { status: 'success', data: FUNDS } })
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

    const before = calls.filter((url) => url === '/auth/dashboard-data').length
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() =>
      expect(calls.filter((url) => url === '/auth/dashboard-data').length).toBe(before + 1)
    )
  })
})
