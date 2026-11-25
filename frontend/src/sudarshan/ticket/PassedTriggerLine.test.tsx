/**
 * v0.17.0 server review L3 (M6.1): Repeat copies a stop's trigger as the order had it, even when
 * the last price has already passed it. Such a stop fills at once in the sandbox (it fills an SL-M
 * at the market the moment Place is pressed), and an owner placing a protective stop again may
 * take it for one that rests. Repeat's ticket now says so before Place, in a line beside the
 * order's, for buy and sell stops, SL and SL-M; in Live it says Upstox may refuse such a stop or
 * take it at once, and how to close now (v0.18.0 verification V3). Nothing else changes: Place
 * stays as it was and sends the same request, and a ticket opened anywhere else shows no such line.
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

const LIVE_TAIL = ' To close a position now, use Market or Limit.'

/** The line in Analyze mode and in Live (v0.18.0 verification V3). */
function both(fields: Partial<StopFields>) {
  return [passedTriggerText(stop(fields), true), passedTriggerText(stop(fields), false)]
}

describe('the words', () => {
  it('say a sell stop the price has passed would fill at once in the sandbox, and in Live never guess', () => {
    expect(both({})).toEqual([
      'The last price 268.40 has already passed the trigger 270.00: in the sandbox this stop would fill at once.',
      `The last price 268.40 has already passed the trigger 270.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ])
  })

  it('say the same of a buy stop below the last price', () => {
    expect(both({ action: 'BUY', trigger: 265 })).toEqual([
      'The last price 268.40 has already passed the trigger 265.00: in the sandbox this stop would fill at once.',
      `The last price 268.40 has already passed the trigger 265.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ])
  })

  it('say an SL whose limit the price is within fills at once, and one past it rests', () => {
    expect(both({ priceType: 'SL', price: 268 })).toEqual([
      'The last price 268.40 has already passed the trigger 270.00: in the sandbox this stop would fill at once.',
      `The last price 268.40 has already passed the trigger 270.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ])
    expect(both({ priceType: 'SL', price: 269.5 })).toEqual([
      'The last price 268.40 has already passed the trigger 270.00: in the sandbox this stop would trigger at once and rest as a limit order at 269.50.',
      `The last price 268.40 has already passed the trigger 270.00: Upstox may refuse such a stop or trigger it at once and rest it as a limit order at 269.50.${LIVE_TAIL}`,
    ])
    expect(both({ action: 'BUY', priceType: 'SL', trigger: 265, price: 270 })).toEqual([
      'The last price 268.40 has already passed the trigger 265.00: in the sandbox this stop would fill at once.',
      `The last price 268.40 has already passed the trigger 265.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ])
    expect(both({ action: 'BUY', priceType: 'SL', trigger: 265, price: 266 })).toEqual([
      'The last price 268.40 has already passed the trigger 265.00: in the sandbox this stop would trigger at once and rest as a limit order at 266.00.',
      `The last price 268.40 has already passed the trigger 265.00: Upstox may refuse such a stop or trigger it at once and rest it as a limit order at 266.00.${LIVE_TAIL}`,
    ])
  })

  it('say an SL with no limit price would trigger at once', () => {
    expect(both({ priceType: 'SL', price: 0 })).toEqual([
      'The last price 268.40 has already passed the trigger 270.00: in the sandbox this stop would trigger at once.',
      `The last price 268.40 has already passed the trigger 270.00: Upstox may refuse such a stop or trigger it at once.${LIVE_TAIL}`,
    ])
  })

  it('say a trigger at the last price is already there, on the tick the order carries', () => {
    const at = [
      'The last price 268.40 is already at the trigger 268.40: in the sandbox this stop would fill at once.',
      `The last price 268.40 is already at the trigger 268.40: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ]
    expect(both({ trigger: 268.4 })).toEqual(at)
    expect(both({ action: 'BUY', trigger: 268.38 })).toEqual(at)
  })

  it('say nothing of a stop not yet passed, of an order that is no stop, or without a price', () => {
    for (const sandbox of [true, false]) {
      const said = (fields: Partial<StopFields>) => passedTriggerText(stop(fields), sandbox)
      expect(said({ trigger: 260 })).toBeNull()
      expect(said({ action: 'BUY', trigger: 275 })).toBeNull()
      expect(said({ action: 'BUY', priceType: 'SL', trigger: 275, price: 276 })).toBeNull()
      for (const priceType of ['MARKET', 'LIMIT']) {
        expect(said({ priceType, price: 280 })).toBeNull()
      }
      expect(said({ ltp: undefined })).toBeNull()
      expect(said({ trigger: 0 })).toBeNull()
    }
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
      `The last price 268.40 has already passed the trigger 270.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ],
    [
      'a sell SL',
      { pricetype: 'SL', price: 269.5 },
      `The last price 268.40 has already passed the trigger 270.00: Upstox may refuse such a stop or trigger it at once and rest it as a limit order at 269.50.${LIVE_TAIL}`,
    ],
    [
      'a buy SL-M',
      { action: 'BUY', trigger_price: 265 },
      `The last price 268.40 has already passed the trigger 265.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
    ],
    [
      'a buy SL',
      { action: 'BUY', pricetype: 'SL', trigger_price: 265, price: 270 },
      `The last price 268.40 has already passed the trigger 265.00: Upstox may refuse such a stop or fill it at once.${LIVE_TAIL}`,
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
