/**
 * v0.17.0 server review L3 (M6.1): Repeat copies a stop's trigger as the order had it, even when
 * the last price has already passed it. Such a stop fills at once (the sandbox fills an SL-M at
 * the market the moment Place is pressed), and an owner placing a protective stop again may take
 * it for one that rests. Repeat's ticket now says so before Place, in a line beside the order's,
 * for buy and sell stops, SL and SL-M. Nothing else changes: Place stays as it was and sends the
 * same request, and a ticket opened anywhere else shows no such line.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { RepeatTicket } from '@/sudarshan/books/cards/OrderSheet'
import type { BookOrder } from '@/sudarshan/books/orderFold'
import { cleanup, render, screen, userEvent, waitFor, within } from '@/test/test-utils'
import { passedTriggerText, type StopFields } from './PassedTriggerLine'

vi.mock('@/sudarshan/watchlist/api', () => ({
  contractOf: async () => ({ lotSize: 1, tickSize: 0.05 }),
}))
vi.mock('@/api/trading', async () => {
  const actual = await vi.importActual<typeof import('@/api/trading')>('@/api/trading')
  return { ...actual, tradingApi: { ...actual.tradingApi, placeOrder: vi.fn() } }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ apiKey: 'test-api-key' }) }))
vi.mock('@/hooks/useLiveQuote', () => ({
  useLiveQuote: () => ({
    data: { ltp: 268.4, close: 270, change: -1.6, changePercent: -0.59, depth: undefined },
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
const LTP = 268.4

beforeEach(() => {
  placeOrder.mockReset()
  placeOrder.mockResolvedValue({ status: 'success', orderid: 'ORD-7' } as never)
})

afterEach(() => {
  cleanup()
})

function stop(fields: Partial<StopFields>): StopFields {
  return {
    action: 'SELL',
    priceType: 'SL-M',
    trigger: 270,
    price: 0,
    ltp: LTP,
    tickSize: 0.05,
    ...fields,
  }
}

describe('the words', () => {
  it('say a sell stop above the last price would fill at once (the decided sentence)', () => {
    expect(passedTriggerText(stop({}))).toBe(
      'The trigger 270.00 is already above the last price 268.40: this stop would fill at once.'
    )
  })

  it('say a buy stop below the last price would fill at once', () => {
    expect(passedTriggerText(stop({ action: 'BUY', trigger: 265 }))).toBe(
      'The trigger 265.00 is already below the last price 268.40: this stop would fill at once.'
    )
  })

  it('say an SL whose limit the price is within fills at once, and one past it rests', () => {
    expect(passedTriggerText(stop({ priceType: 'SL', price: 268 }))).toBe(
      'The trigger 270.00 is already above the last price 268.40: this stop would fill at once.'
    )
    expect(passedTriggerText(stop({ priceType: 'SL', price: 269.5 }))).toBe(
      'The trigger 270.00 is already above the last price 268.40: this stop would trigger at once and rest as a limit order at 269.50.'
    )
    expect(
      passedTriggerText(stop({ action: 'BUY', priceType: 'SL', trigger: 265, price: 270 }))
    ).toBe(
      'The trigger 265.00 is already below the last price 268.40: this stop would fill at once.'
    )
    expect(
      passedTriggerText(stop({ action: 'BUY', priceType: 'SL', trigger: 265, price: 266 }))
    ).toBe(
      'The trigger 265.00 is already below the last price 268.40: this stop would trigger at once and rest as a limit order at 266.00.'
    )
  })

  it('say a trigger at the last price is already there, on the tick the order carries', () => {
    expect(passedTriggerText(stop({ trigger: 268.4 }))).toBe(
      'The trigger 268.40 is already at the last price 268.40: this stop would fill at once.'
    )
    expect(passedTriggerText(stop({ action: 'BUY', trigger: 268.38 }))).toBe(
      'The trigger 268.40 is already at the last price 268.40: this stop would fill at once.'
    )
  })

  it('say nothing of a stop not yet passed, of an order that is no stop, or without a price', () => {
    expect(passedTriggerText(stop({ trigger: 260 }))).toBeNull()
    expect(passedTriggerText(stop({ action: 'BUY', trigger: 275 }))).toBeNull()
    expect(
      passedTriggerText(stop({ action: 'BUY', priceType: 'SL', trigger: 275, price: 276 }))
    ).toBeNull()
    for (const priceType of ['MARKET', 'LIMIT']) {
      expect(passedTriggerText(stop({ priceType, price: 280 }))).toBeNull()
    }
    expect(passedTriggerText(stop({ ltp: undefined }))).toBeNull()
    expect(passedTriggerText(stop({ trigger: 0 }))).toBeNull()
  })
})

function bookOrder(fields: Partial<BookOrder>): BookOrder {
  return {
    symbol: 'ENGINERSIN',
    exchange: 'NSE',
    action: 'SELL',
    quantity: 2,
    price: 0,
    trigger_price: 270,
    pricetype: 'SL-M',
    product: 'CNC',
    orderid: '4001',
    order_status: 'cancelled',
    ...fields,
  } as unknown as BookOrder
}

async function repeat(order: BookOrder) {
  render(<RepeatTicket order={order} apiKey="test-api-key" onClose={() => {}} />)
  return screen.findByRole('dialog', { name: /^Place Order/ })
}

const PLACE = { name: /^Place (BUY|SELL) Order$/ }

describe("Repeat's ticket", () => {
  it.each([
    [
      'a sell SL-M',
      {},
      'The trigger 270.00 is already above the last price 268.40: this stop would fill at once.',
    ],
    [
      'a sell SL',
      { pricetype: 'SL', price: 269.5 },
      'The trigger 270.00 is already above the last price 268.40: this stop would trigger at once and rest as a limit order at 269.50.',
    ],
    [
      'a buy SL-M',
      { action: 'BUY', trigger_price: 265 },
      'The trigger 265.00 is already below the last price 268.40: this stop would fill at once.',
    ],
    [
      'a buy SL',
      { action: 'BUY', pricetype: 'SL', trigger_price: 265, price: 270 },
      'The trigger 265.00 is already below the last price 268.40: this stop would fill at once.',
    ],
  ])('says before Place that %s the price has passed would fill or trigger at once', async (_, fields, words) => {
    const order = bookOrder(fields as Partial<BookOrder>)
    const ticket = await repeat(order)
    const line = within(ticket).getByTestId('ticket-trigger-passed')
    expect(line).toHaveTextContent(words)
    const place = within(ticket).getByRole('button', PLACE)
    expect(place.getAttribute('aria-describedby')?.split(' ')).toContain(line.id)
    expect(placeOrder).not.toHaveBeenCalled()

    // Place is as it was, and sends the order as Repeat copied it.
    await userEvent.click(place)
    await waitFor(() => expect(placeOrder).toHaveBeenCalledTimes(1))
    const type = order.pricetype as string
    expect(placeOrder.mock.calls[0][0]).toEqual({
      apikey: 'test-api-key',
      strategy: 'Orders',
      symbol: 'ENGINERSIN',
      exchange: 'NSE',
      action: order.action,
      quantity: 2,
      pricetype: type,
      product: 'CNC',
      ...(type === 'SL' ? { price: order.price } : {}),
      trigger_price: order.trigger_price,
    })
  })

  it.each([
    ['a sell stop below the price', {}, 260],
    ['a buy stop above the price', { action: 'BUY' }, 275],
  ])('shows no line for %s', async (_, fields, trigger) => {
    const ticket = await repeat(
      bookOrder({ ...(fields as Partial<BookOrder>), trigger_price: trigger })
    )
    expect(within(ticket).queryByTestId('ticket-trigger-passed')).toBeNull()
    expect(within(ticket).getByRole('button', PLACE).getAttribute('aria-describedby')).not.toMatch(
      /passed/
    )
  })

  it('drops the line once the trigger is moved below the price', async () => {
    const ticket = await repeat(bookOrder({}))
    expect(within(ticket).getByTestId('ticket-trigger-passed')).toBeInTheDocument()
    const trigger = within(ticket).getByRole('spinbutton', { name: 'Trigger Price' })
    await userEvent.clear(trigger)
    await userEvent.type(trigger, '260')
    await waitFor(() => expect(within(ticket).queryByTestId('ticket-trigger-passed')).toBeNull())
  })
})

describe('a ticket opened anywhere else', () => {
  it('shows no such line for the same stop', async () => {
    render(
      <PlaceOrderDialog
        open
        onOpenChange={() => {}}
        symbol="ENGINERSIN"
        exchange="NSE"
        action="SELL"
        quantity={2}
        product="CNC"
        priceType="SL-M"
        triggerPrice={270}
      />
    )
    const ticket = await screen.findByRole('dialog', { name: /^Place Order/ })
    expect(within(ticket).getByTestId('ticket-summary')).toHaveTextContent('trigger 270.00')
    expect(within(ticket).queryByTestId('ticket-trigger-passed')).toBeNull()
  })
})
