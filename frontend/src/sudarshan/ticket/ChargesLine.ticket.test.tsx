/**
 * The charges line in the order ticket (B6: M5.10), in upstream's PlaceOrderDialog: it sits
 * after the margin line, reads the estimate for the order as the ticket holds it, says
 * "not read" with the journal switched off, and the order Place sends is exactly the one it
 * sent before the line: the line is an entry point to nothing.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'

const journal = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/journal/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/journal/api')>()),
  journalCall: (...args: unknown[]) => journal.call(...args),
}))
// The margin line's reads: answered here, so nothing leaves the test.
vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  apiClient: { post: vi.fn(async () => ({ data: { status: 'error' } })) },
}))
vi.mock('@/utils/toast', () => ({
  showToast: { error: vi.fn(), success: vi.fn() },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key' }),
}))
vi.mock('@/hooks/useLiveQuote', () => ({
  useLiveQuote: () => ({
    data: {
      ltp: 100,
      close: 99,
      change: 1,
      changePercent: 1.01,
      bidPrice: 99.95,
      askPrice: 100.05,
      bidSize: 10,
      askSize: 10,
      depth: undefined,
    },
    isLive: false,
    isConnected: false,
    isLoading: false,
    isPaused: false,
    isFallbackMode: false,
    dataSource: 'none',
    refresh: async () => {},
  }),
}))

const ticket = {
  open: true,
  onOpenChange: () => {},
  symbol: 'NIFTY28MAR2420800CE',
  exchange: 'NFO',
  action: 'BUY' as const,
  quantity: 150,
  lotSize: 75,
  tickSize: 0.05,
  product: 'NRML' as const,
  priceType: 'MARKET' as const,
  strategy: 'chart-trading',
}

const estimate = {
  segment: 'options',
  modelled: true,
  plan: 'plus',
  parts: {
    brokerage_paise: 3000,
    stt_paise: 0,
    exchange_paise: 533,
    sebi_paise: 2,
    stamp_paise: 45,
    gst_paise: 636,
    total_paise: 4216,
  },
  turnover_paise: 1500000,
  day: 20261116,
  rate_card: { version: '2026-11-14.2', from_day: 20260401 },
  left_out: [],
}

beforeEach(() => {
  journal.call.mockReset().mockResolvedValue({ enabled: true, data: estimate })
})

describe('the charges line in the order ticket', () => {
  it('sits after the margin line and reads the order as the ticket holds it', async () => {
    render(<PlaceOrderDialog {...ticket} />)
    const charges = screen.getByTestId('charges-line')
    const margin = screen.getByTestId('margin-line')
    expect(margin.compareDocumentPosition(charges) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByTestId('ticket-body').contains(charges)).toBe(true)
    await waitFor(() => expect(charges).toHaveTextContent('Charges est. ₹42.16'))
    expect(journal.call).toHaveBeenCalledTimes(1)
    expect(journal.call.mock.calls[0]).toEqual([
      'charges_estimate',
      {
        exchange: 'NFO',
        symbol: 'NIFTY28MAR2420800CE',
        action: 'BUY',
        quantity: 150,
        price_u: 1_000_000,
        product: 'NRML',
      },
    ])
  })

  it('reads again when the side changes in the ticket', async () => {
    render(<PlaceOrderDialog {...ticket} />)
    await waitFor(() => expect(journal.call).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('button', { name: 'SELL' }))
    await waitFor(() => expect(journal.call).toHaveBeenCalledTimes(2))
    expect(journal.call.mock.calls[1][1]).toMatchObject({ action: 'SELL' })
  })

  it('says "not read" with the journal switched off, and Place sends the order it always sent', async () => {
    journal.call.mockResolvedValue({ enabled: false, data: null, message: 'Off.' })
    const place = vi.fn(async () => ({ orderId: 'ORD-1' }))
    render(<PlaceOrderDialog {...ticket} place={place} />)
    await waitFor(() =>
      expect(screen.getByTestId('charges-line')).toHaveTextContent('Charges est. not read')
    )
    await userEvent.click(screen.getByRole('button', { name: 'Place BUY Order' }))
    await waitFor(() => expect(place).toHaveBeenCalledTimes(1))
    expect(place).toHaveBeenCalledWith({
      symbol: 'NIFTY28MAR2420800CE',
      exchange: 'NFO',
      action: 'BUY',
      quantity: 150,
      pricetype: 'MARKET',
      product: 'NRML',
    })
  })
})
