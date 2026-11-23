/**
 * Test day TD5 (M6.1): Repeat on a resting BUY LIMIT 10.05 opened the ticket as MARKET, the
 * ticket's own default, so one quick tap on Place sent a buy at the market instead of the limit.
 * Repeat now copies the order's price type, and its price and trigger on the tick, with the side,
 * quantity and product it already copied. Golden vectors (`repeat_vectors.json`) through the pure
 * part and through the real ticket: the ticket as it opens, nothing sent until Place, and the
 * request Place sends equal to the original order's own fields.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import type { BookOrder } from '@/sudarshan/books/orderFold'
import { cleanup, render, screen, userEvent, waitFor, within } from '@/test/test-utils'
import { RepeatTicket, repeatPrice } from './OrderSheet'
import vectors from './repeat_vectors.json'

const contracts = vi.hoisted(() => new Map<string, { lotSize: number; tickSize: number }>())
vi.mock('@/sudarshan/watchlist/api', () => ({
  contractOf: async (_key: string, symbol: string) => {
    const found = contracts.get(symbol)
    if (!found) throw new Error('Contract not found')
    return found
  },
}))
vi.mock('@/api/trading', async () => {
  const actual = await vi.importActual<typeof import('@/api/trading')>('@/api/trading')
  return { ...actual, tradingApi: { ...actual.tradingApi, placeOrder: vi.fn() } }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ apiKey: 'test-api-key' }) }))
vi.mock('@/hooks/useLiveQuote', () => ({
  useLiveQuote: () => ({
    // A last price far from every order's own, so a price taken from it would show.
    data: { ltp: 777.7, close: 770, change: 7.7, changePercent: 1, depth: undefined },
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

type Case = (typeof vectors.cases)[number]
const LABEL_TO_TYPE: Record<string, string> = {
  Market: 'MARKET',
  Limit: 'LIMIT',
  'SL-L': 'SL',
  'SL-M': 'SL-M',
}

/** The order's own fields, as an order request names them: what Repeat promises to place again. */
function ownFields(order: Case['order']): Record<string, unknown> {
  const type = order.pricetype
  return {
    symbol: order.symbol,
    exchange: order.exchange,
    action: order.action,
    quantity: order.quantity,
    pricetype: type,
    product: order.product,
    ...(type === 'LIMIT' || type === 'SL' ? { price: order.price } : {}),
    ...(type === 'SL' || type === 'SL-M' ? { trigger_price: order.trigger_price } : {}),
  }
}

beforeEach(() => {
  contracts.clear()
  placeOrder.mockReset()
  placeOrder.mockResolvedValue({ status: 'success', orderid: 'ORD-9' } as never)
})

afterEach(() => {
  cleanup()
})

describe.each(vectors.cases)('Repeat of $name', (vector) => {
  const order = vector.order as unknown as BookOrder

  it('copies the price type, price and trigger on the tick', () => {
    expect(repeatPrice(order, vector.contract.tickSize)).toEqual({
      priceType: LABEL_TO_TYPE[vector.ticket.priceType],
      ...(vector.ticket.price === null ? {} : { price: vector.ticket.price }),
      ...(vector.ticket.trigger === null ? {} : { triggerPrice: vector.ticket.trigger }),
    })
  })

  it('opens the ticket as the order was, sends nothing until Place, then sends the order', async () => {
    contracts.set(vector.order.symbol, vector.contract)
    render(<RepeatTicket order={order} apiKey="test-api-key" onClose={() => {}} />)
    const ticket = await screen.findByRole('dialog', { name: /^Place Order/ })
    const side = within(within(ticket).getByRole('group', { name: 'Action' })).getByRole('button', {
      pressed: true,
    })
    expect(side).toHaveTextContent(vector.ticket.action)
    expect(within(ticket).getByRole('spinbutton', { name: 'Quantity' })).toHaveValue(
      vector.ticket.quantity
    )
    expect(within(ticket).getByRole('combobox', { name: 'Price Type' })).toHaveTextContent(
      vector.ticket.priceType
    )
    expect(within(ticket).getByRole('combobox', { name: 'Product' })).toHaveTextContent(
      vector.ticket.product
    )
    const price = within(ticket).queryByRole('spinbutton', { name: 'Price', exact: true })
    if (vector.ticket.price === null) expect(price).toBeNull()
    else expect(price).toHaveValue(vector.ticket.price)
    const trigger = within(ticket).queryByRole('spinbutton', { name: 'Trigger Price' })
    if (vector.ticket.trigger === null) expect(trigger).toBeNull()
    else expect(trigger).toHaveValue(vector.ticket.trigger)
    // The line beside Place says the order copied, whatever the body has scrolled to.
    expect(within(ticket).getByTestId('ticket-summary')).toHaveTextContent(vector.ticket.line)
    expect(placeOrder).not.toHaveBeenCalled()

    await userEvent.click(within(ticket).getByRole('button', { name: /^Place (BUY|SELL) Order$/ }))
    await waitFor(() => expect(placeOrder).toHaveBeenCalledTimes(1))
    const sent = placeOrder.mock.calls[0][0]
    expect(sent).toEqual({ apikey: 'test-api-key', strategy: 'Orders', ...vector.sends })
    // On the tick, the request is the original order itself; off it, the tick's price.
    if (!('offTick' in vector)) {
      const { apikey: _key, strategy: _tag, ...fields } = sent as unknown as Record<string, unknown>
      expect(fields).toEqual(ownFields(vector.order))
    }
  })
})

describe('Repeat of a type the ticket does not take', () => {
  it('copies nothing, and the ticket opens as it always has', () => {
    const odd = { ...vectors.cases[1].order, pricetype: 'BRACKET' } as unknown as BookOrder
    expect(repeatPrice(odd, 0.1)).toEqual({})
  })

  it('leaves out a price or trigger the book row does not carry', () => {
    const bare = {
      ...vectors.cases[2].order,
      price: 0,
      trigger_price: null,
    } as unknown as BookOrder
    expect(repeatPrice(bare, 0.1)).toEqual({ priceType: 'SL' })
  })
})
