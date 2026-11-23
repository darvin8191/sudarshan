/**
 * SUDARSHAN M20.2 (v0.17.0 verification V5): below 360 px with Larger text the ticket's title
 * drops "Place Order -" on screen (`sd-ticket-lead` in theme.css), so the side and the symbol
 * share one line and the first screen holds more of the fields. What the ticket sends does not
 * change: the same inputs send the bodies below, written down from the ticket before the change,
 * at either text size, either grouping and either window width. A screen reader still hears the
 * whole title: the dialog keeps its name.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import { setNumberGrouping } from '@/sudarshan/dashboard/numberStyle'
import { cleanup, fireEvent, render, screen, userEvent, waitFor } from '@/test/test-utils'
import { PlaceOrderDialog } from './PlaceOrderDialog'

vi.mock('@/api/trading', async () => {
  const actual = await vi.importActual<typeof import('@/api/trading')>('@/api/trading')
  return { ...actual, tradingApi: { ...actual.tradingApi, placeOrder: vi.fn() } }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ apiKey: 'test-api-key' }) }))
vi.mock('@/hooks/useLiveQuote', () => ({
  useLiveQuote: () => ({
    data: {
      ltp: 100,
      close: 99,
      change: 1,
      changePercent: 1.01,
      bidPrice: 99.95,
      askPrice: 100.05,
      bidSize: 124000,
      askSize: 1250,
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

const placeOrder = vi.mocked(tradingApi.placeOrder)

type Props = Parameters<typeof PlaceOrderDialog>[0]
type Step = () => Promise<void>

const nifty: Props = {
  open: true,
  onOpenChange: () => {},
  symbol: 'NIFTY17NOV2625900CE',
  exchange: 'NFO',
  action: 'BUY',
  quantity: 65,
  lotSize: 65,
  tickSize: 0.05,
  product: 'MIS',
  priceType: 'MARKET',
  strategy: 'chart-trading',
}
const sbin: Props = {
  ...nifty,
  symbol: 'SBIN',
  exchange: 'NSE',
  quantity: 1234,
  lotSize: 1,
  product: 'CNC',
  priceType: 'LIMIT',
  price: 803.8,
}

const type = async (name: string, value: string) => {
  const box = screen.getByRole('spinbutton', { name })
  fireEvent.change(box, { target: { value } })
  fireEvent.blur(box)
}
const press = (name: string) => userEvent.click(screen.getByRole('button', { name }))

const head = { apikey: 'test-api-key', strategy: 'chart-trading' }
const option = { ...head, exchange: 'NFO', symbol: 'NIFTY17NOV2625900CE' }
const share = { ...head, exchange: 'NSE', symbol: 'SBIN' }

/** Each case: the ticket as opened, what is done in it, and the body it sent before V5. */
const CASES: Array<[string, Props, Step[], Record<string, unknown>]> = [
  [
    'NIFTY as opened',
    nifty,
    [],
    { ...option, action: 'BUY', quantity: 65, pricetype: 'MARKET', product: 'MIS' },
  ],
  [
    'NIFTY 3 lots',
    nifty,
    [() => type('Quantity', '3')],
    { ...option, action: 'BUY', quantity: 195, pricetype: 'MARKET', product: 'MIS' },
  ],
  [
    'NIFTY 130 shares',
    nifty,
    [() => press('Shares'), () => type('Quantity', '130')],
    { ...option, action: 'BUY', quantity: 130, pricetype: 'MARKET', product: 'MIS' },
  ],
  [
    'NIFTY sell 2 lots',
    nifty,
    [() => press('SELL'), () => type('Quantity', '2')],
    { ...option, action: 'SELL', quantity: 130, pricetype: 'MARKET', product: 'MIS' },
  ],
  [
    'NIFTY SL',
    { ...nifty, priceType: 'SL', price: 101.03, triggerPrice: 100.52 },
    [],
    {
      ...option,
      action: 'BUY',
      quantity: 65,
      pricetype: 'SL',
      product: 'MIS',
      price: 101.05,
      trigger_price: 100.5,
    },
  ],
  [
    'NIFTY SL-M 10 lots',
    { ...nifty, priceType: 'SL-M', triggerPrice: 99.97 },
    [() => type('Quantity', '10')],
    {
      ...option,
      action: 'BUY',
      quantity: 650,
      pricetype: 'SL-M',
      product: 'MIS',
      trigger_price: 99.95,
    },
  ],
  [
    'SBIN limit 1,234',
    sbin,
    [],
    { ...share, action: 'BUY', quantity: 1234, pricetype: 'LIMIT', product: 'CNC', price: 803.8 },
  ],
  [
    'SBIN limit, 1,50,000 at 803.83 typed',
    sbin,
    [() => type('Quantity', '150000'), () => type('Price', '803.83')],
    {
      ...share,
      action: 'BUY',
      quantity: 150000,
      pricetype: 'LIMIT',
      product: 'CNC',
      price: 803.85,
    },
  ],
  [
    'SBIN market sell',
    { ...sbin, action: 'SELL', priceType: 'MARKET' },
    [],
    { ...share, action: 'SELL', quantity: 1234, pricetype: 'MARKET', product: 'CNC' },
  ],
]

describe.each([
  ['indian', 'normal', 412],
  ['indian', 'large', 320],
  ['international', 'large', 320],
  ['international', 'normal', 360],
] as const)('grouping %s, text %s, a %i px window', (grouping, text, width) => {
  let wide = 0
  beforeEach(() => {
    wide = window.innerWidth
    setNumberGrouping(grouping)
    if (text === 'large') document.documentElement.dataset.text = 'large'
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
  })
  afterEach(() => {
    setNumberGrouping('indian')
    delete document.documentElement.dataset.text
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: wide })
    cleanup()
  })

  it.each(CASES)('%s: sends what it sent before', async (_name, props, steps, body) => {
    placeOrder.mockReset()
    placeOrder.mockResolvedValue({ status: 'success', orderid: 'ORD-1' } as never)
    const onSuccess = vi.fn()
    render(<PlaceOrderDialog {...props} onSuccess={onSuccess} />)
    for (const step of steps) await step()
    await userEvent.click(screen.getByRole('button', { name: /^Place (BUY|SELL) Order$/ }))
    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
    expect(placeOrder).toHaveBeenCalledTimes(1)
    expect(placeOrder.mock.calls[0][0]).toEqual(body)
    expect(typeof placeOrder.mock.calls[0][0].quantity).toBe('number')
  })

  it('keeps the whole title for a screen reader, its lead the part theme.css lets go', () => {
    render(<PlaceOrderDialog {...sbin} />)
    // A browser puts a space between the title's flex items; jsdom does not lay them out.
    const ticket = screen.getByRole('dialog', { name: /^Place Order - ?BUY ?SBIN$/ })
    const lead = ticket.querySelector('.sd-ticket-lead')
    expect(lead?.textContent).toBe('Place Order -')
    expect(lead?.closest('h2')?.textContent).toBe('Place Order -BUYSBIN')
  })
})
