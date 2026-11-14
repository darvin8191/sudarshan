/**
 * The order ticket the chart opens while One-Click is off. What is pinned:
 * the Lots box reads the lots the ticket carries (a 150-unit ticket on a
 * 75-lot contract is 2, not 1, and it places 150 either way); a caller's own
 * route takes the confirmed order instead of the bare API post; and the
 * dialog can be portalled into the caller, for a fullscreen pane.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import { noteKillSwitch, resetKillSwitchForTests } from '@/sudarshan/risk/killSwitch'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import { showToast } from '@/utils/toast'
import { lotsFor, orderLine, PlaceOrderDialog } from './PlaceOrderDialog'

vi.mock('@/api/trading', async () => {
  const actual = await vi.importActual<typeof import('@/api/trading')>('@/api/trading')
  return {
    ...actual,
    tradingApi: { ...actual.tradingApi, placeOrder: vi.fn() },
  }
})

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

const placeOrder = vi.mocked(tradingApi.placeOrder)

const nifty = {
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

describe('lotsFor', () => {
  it('reads the lot count out of a quantity in units', () => {
    expect(lotsFor(150, 75)).toBe(2)
    expect(lotsFor(75, 75)).toBe(1)
    expect(lotsFor(1, 1)).toBe(1)
  })

  it('is one lot when nothing was supplied, as the option chain relies on', () => {
    expect(lotsFor(undefined, 75)).toBe(1)
    expect(lotsFor(150, 0)).toBe(1)
  })
})

describe('PlaceOrderDialog', () => {
  beforeEach(() => {
    placeOrder.mockReset()
  })

  it('scrolls its body inside the window so Place and Cancel stay reachable on a short screen', () => {
    render(<PlaceOrderDialog {...nifty} priceType="SL" />)
    const body = screen.getByTestId('ticket-body')
    expect(body.className).toContain('overflow-y-auto')
    const dialog = screen.getByRole('dialog')
    expect(dialog.className).toContain('max-h-[calc(100dvh-1rem)]')
    expect(dialog.className).toContain('overflow-hidden')
    // The buttons sit outside the scrolling body.
    expect(body.contains(screen.getByRole('button', { name: 'Place BUY Order' }))).toBe(false)
    expect(body.contains(screen.getByRole('button', { name: 'Cancel' }))).toBe(false)
  })

  // SUDARSHAN (SCR-P item 7): the three number boxes had no accessible name, so a screen reader
  // read "spin button" for each and the browser specs found the price by its position.
  it('names its quantity, price and trigger boxes by their labels', () => {
    render(<PlaceOrderDialog {...nifty} priceType="SL" />)
    const boxes = screen.getAllByRole('spinbutton')
    expect(boxes).toHaveLength(3)
    expect(screen.getByRole('spinbutton', { name: 'Quantity' })).toBe(boxes[0])
    expect(screen.getByRole('spinbutton', { name: 'Price' })).toBe(boxes[1])
    expect(screen.getByRole('spinbutton', { name: 'Trigger Price' })).toBe(boxes[2])
    // One box a label: none is named twice, none shares an id.
    expect(new Set(boxes.map((box) => box.id)).size).toBe(3)
  })

  it('names the quantity box alone on a market order', () => {
    render(<PlaceOrderDialog {...nifty} />)
    expect(screen.getByRole('spinbutton', { name: 'Quantity' })).toHaveValue(2)
    expect(screen.queryByRole('spinbutton', { name: 'Price' })).toBeNull()
    expect(screen.queryByRole('spinbutton', { name: 'Trigger Price' })).toBeNull()
  })

  it('shows the lots a units quantity stands for, over the same total', () => {
    render(<PlaceOrderDialog {...nifty} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(2)
    expect(screen.getByText('Total qty: 150')).toBeInTheDocument()
  })

  it('sends the confirmed order through the caller route and never the API', async () => {
    const place = vi.fn(async () => ({ orderId: 'ORD-1' }))
    const onSuccess = vi.fn()
    render(<PlaceOrderDialog {...nifty} place={place} onSuccess={onSuccess} />)
    await userEvent.click(screen.getByRole('button', { name: 'Place BUY Order' }))
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('ORD-1'))
    expect(place).toHaveBeenCalledWith({
      symbol: 'NIFTY28MAR2420800CE',
      exchange: 'NFO',
      action: 'BUY',
      quantity: 150,
      pricetype: 'MARKET',
      product: 'NRML',
    })
    expect(placeOrder).not.toHaveBeenCalled()
  })

  it('shows the route refusal and keeps the ticket open', async () => {
    const reason = 'caller expects live mode but the OpenAlgo server is in analyzer mode'
    const place = vi.fn(async () => {
      throw new Error(reason)
    })
    const onError = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <PlaceOrderDialog {...nifty} place={place} onError={onError} onOpenChange={onOpenChange} />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Place BUY Order' }))
    await waitFor(() => expect(onError).toHaveBeenCalledWith(reason))
    expect(showToast.error).toHaveBeenCalledWith(reason, 'orders')
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(placeOrder).not.toHaveBeenCalled()
  })

  it('posts through the API with the key and strategy when no route is given', async () => {
    placeOrder.mockResolvedValue({ status: 'success', orderid: 'ORD-2' } as never)
    const onSuccess = vi.fn()
    render(<PlaceOrderDialog {...nifty} onSuccess={onSuccess} />)
    await userEvent.click(screen.getByRole('button', { name: 'Place BUY Order' }))
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('ORD-2'))
    expect(placeOrder).toHaveBeenCalledWith(
      expect.objectContaining({
        apikey: 'test-api-key',
        strategy: 'chart-trading',
        quantity: 150,
      })
    )
  })

  it('renders inside the container it is given', () => {
    const host = document.createElement('div')
    host.id = 'fullscreen-pane'
    document.body.appendChild(host)
    try {
      render(<PlaceOrderDialog {...nifty} container={host} />)
      expect(host.querySelector('[role="dialog"]')).not.toBeNull()
    } finally {
      host.remove()
    }
  })
})

// SUDARSHAN v0.13.0 review H1 and M2: what the ticket shows on a phone. None of it changes what
// the ticket sends (the tests above pin that).
describe('PlaceOrderDialog on a phone', () => {
  const comesBefore = (first: Element, second: Element) =>
    Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)

  it('puts price type, product, price and trigger straight under quantity, before Size by risk (H1)', () => {
    render(<PlaceOrderDialog {...nifty} priceType="SL" price={101} triggerPrice={100.5} />)
    const quantity = screen.getByRole('spinbutton', { name: 'Quantity' })
    const type = screen.getByRole('combobox', { name: 'Price Type' })
    const price = screen.getByRole('spinbutton', { name: 'Price' })
    const trigger = screen.getByRole('spinbutton', { name: 'Trigger Price' })
    const risk = screen.getByRole('button', { name: 'Size by risk' })
    const depth = screen.getByRole('button', { name: /Market Depth/ })
    expect(comesBefore(quantity, type)).toBe(true)
    expect(comesBefore(type, price)).toBe(true)
    expect(comesBefore(price, trigger)).toBe(true)
    expect(comesBefore(trigger, risk)).toBe(true)
    expect(comesBefore(risk, depth)).toBe(true)
  })

  it('says the order in one line beside Place, outside the scrolling body (H1)', async () => {
    render(
      <PlaceOrderDialog
        {...nifty}
        symbol="SBIN"
        exchange="NSE"
        action="SELL"
        quantity={1}
        lotSize={1}
        product="CNC"
        priceType="LIMIT"
        price={803}
      />
    )
    const line = screen.getByTestId('ticket-summary')
    expect(line).toHaveTextContent('SELL 1 SBIN · LIMIT 803.00 · CNC')
    expect(screen.getByTestId('ticket-body').contains(line)).toBe(false)
    expect(screen.getByRole('button', { name: 'Place SELL Order' })).toHaveAccessibleDescription(
      'SELL 1 SBIN · LIMIT 803.00 · CNC'
    )
    // It follows the form: a tick up, then the other side.
    await userEvent.click(screen.getByRole('button', { name: 'Raise the price by one tick' }))
    await userEvent.click(screen.getByRole('button', { name: 'BUY' }))
    expect(line).toHaveTextContent('BUY 1 SBIN · LIMIT 803.05 · CNC')
  })

  it('keeps a price it was opened with when the last price is already known, and sends it', async () => {
    const place = vi.fn().mockResolvedValue({ orderId: '1' })
    render(
      <PlaceOrderDialog
        {...nifty}
        symbol="SBIN"
        exchange="NSE"
        quantity={1}
        lotSize={1}
        product="CNC"
        priceType="LIMIT"
        price={803}
        place={place}
      />
    )
    // The quote says 100: the box and the order keep the 803 that was asked for.
    expect(screen.getByRole('spinbutton', { name: 'Price' })).toHaveValue(803)
    await userEvent.click(screen.getByRole('button', { name: 'Place BUY Order' }))
    await waitFor(() => expect(place).toHaveBeenCalledTimes(1))
    expect(place.mock.calls[0][0]).toMatchObject({ pricetype: 'LIMIT', price: 803 })
  })

  it('still fills a limit with the last price when it was opened with none', () => {
    render(<PlaceOrderDialog {...nifty} priceType="LIMIT" />)
    expect(screen.getByRole('spinbutton', { name: 'Price' })).toHaveValue(100)
  })

  it('words every price type in the line, with lots beside the units', () => {
    const base = {
      action: 'BUY' as const,
      quantity: 150,
      lots: 2,
      symbol: 'NIFTY28MAR2420800CE',
      price: 101,
      trigger: 100.5,
      product: 'NRML',
    }
    const said = (priceType: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M') => {
      const line = orderLine({ ...base, priceType })
      return `${line.side} ${line.rest}`
    }
    expect(said('MARKET')).toBe('BUY 150 (2 lots) NIFTY28MAR2420800CE · MARKET · NRML')
    expect(said('LIMIT')).toBe('BUY 150 (2 lots) NIFTY28MAR2420800CE · LIMIT 101.00 · NRML')
    expect(said('SL')).toBe(
      'BUY 150 (2 lots) NIFTY28MAR2420800CE · SL-L 101.00, trigger 100.50 · NRML'
    )
    expect(said('SL-M')).toBe('BUY 150 (2 lots) NIFTY28MAR2420800CE · SL-M, trigger 100.50 · NRML')
    expect(orderLine({ ...base, lots: 1, quantity: 75, priceType: 'MARKET' }).rest).toContain(
      '75 (1 lot) '
    )
  })

  it('names the Action, Price Type, Product and Exchange pickers and the steppers (M2)', () => {
    render(<PlaceOrderDialog {...nifty} symbol="SBIN" exchange="NSE" lotSize={1} priceType="SL" />)
    const action = screen.getByRole('group', { name: 'Action' })
    expect(action).toContainElement(screen.getByRole('button', { name: 'BUY', pressed: true }))
    expect(action).toContainElement(screen.getByRole('button', { name: 'SELL', pressed: false }))
    expect(screen.getByRole('combobox', { name: 'Price Type' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Product' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Exchange' })).toBeInTheDocument()
    for (const name of [
      'Lower the price by one tick',
      'Raise the price by one tick',
      'Lower the trigger price by one tick',
      'Raise the trigger price by one tick',
      'Close',
    ])
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })

  it('gives every control a 48 dp box (M2)', () => {
    render(<PlaceOrderDialog {...nifty} priceType="SL" />)
    const tall = (element: HTMLElement) =>
      /(^|\s|:)(h-12|size-12|min-h-12)(\s|$)/.test(element.className)
    for (const name of ['Place BUY Order', 'Cancel', 'BUY', 'SELL', 'Lots', 'Shares', 'Close'])
      expect(tall(screen.getByRole('button', { name })), name).toBe(true)
    for (const name of ['Price Type', 'Product'])
      expect(tall(screen.getByRole('combobox', { name })), name).toBe(true)
    for (const box of screen.getAllByRole('spinbutton')) expect(tall(box)).toBe(true)
    expect(screen.getByRole('button', { name: 'Lots', pressed: true })).toBeInTheDocument()
  })

  it('colours the side from --buy-solid and --sell-solid, and keeps no upstream green or red (M2)', async () => {
    render(<PlaceOrderDialog {...nifty} />)
    const place = () => screen.getByRole('button', { name: /^Place (BUY|SELL) Order$/ })
    expect(place().className).toContain('bg-[color:var(--buy-solid)]')
    await userEvent.click(screen.getByRole('button', { name: 'SELL' }))
    expect(place().className).toContain('bg-[color:var(--sell-solid)]')
    // Never colour alone: the side is a word in the title, on the button and in the line.
    expect(screen.getByRole('dialog')).toHaveAccessibleName(/Place Order -\s*SELL/)
    expect(screen.getByTestId('ticket-summary')).toHaveTextContent(/^SELL /)
    for (const file of ['PlaceOrderDialog.tsx', 'QuoteHeader.tsx', 'MarketDepthPanel.tsx']) {
      const source = readFileSync(join(process.cwd(), 'src/components/trading', file), 'utf8')
      expect(
        source.match(/(?:green|red|emerald|rose|purple|amber|pink|cyan|orange|blue)-\d{3}/g) ?? [],
        file
      ).toEqual([])
    }
  })
})

describe('PlaceOrderDialog while the kill switch is on', () => {
  beforeEach(() => resetKillSwitchForTests())

  it('says on the ticket that a new order is refused, and disables nothing', () => {
    noteKillSwitch({ known: true, on: true }, 'on')
    render(<PlaceOrderDialog {...nifty} />)
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent(
      'Kill switch on: a new order is refused'
    )
    for (const button of screen.getAllByRole('button', { name: /buy/i })) {
      expect(button).toBeEnabled()
    }
  })

  it('says only that the sandbox is covered when the guard is at the sandbox stage', () => {
    noteKillSwitch({ known: true, on: true }, 'sandbox')
    render(<PlaceOrderDialog {...nifty} />)
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent(
      'for the sandbox only: Live orders are not checked'
    )
  })

  it('has no line while it is off', () => {
    noteKillSwitch({ known: true, on: false }, 'on')
    render(<PlaceOrderDialog {...nifty} />)
    expect(screen.queryByTestId('kill-switch-note')).toBeNull()
  })
})

describe('PlaceOrderDialog: size by risk (B4)', () => {
  beforeEach(() => {
    placeOrder.mockReset()
    localStorage.clear()
  })

  it('is folded until opened, then fills whole lots into the quantity and sends nothing', async () => {
    render(<PlaceOrderDialog {...nifty} />)
    // Folded: the quantity is still the ticket's one number box.
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Size by risk' }))
    await userEvent.type(screen.getByLabelText('Risk (₹)'), '4500')
    await userEvent.type(screen.getByLabelText('Stop price'), '80')
    // The last price is 100, so a stop at 80 is 20 a unit, 1,500 a lot of 75.
    expect(screen.getByTestId('risk-size')).toHaveTextContent(
      '3 lots (225) risk ₹4,500.00, 20.00 a unit to the stop'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Set quantity to 3 lots' }))
    expect(screen.getByText('Total qty: 225')).toBeInTheDocument()
    expect(placeOrder).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('sudarshan-ticket-risk') ?? '{}')).toEqual({
      fno: 4500,
    })
  })

  it('says where a buy’s stop goes, and offers nothing to fill', async () => {
    render(<PlaceOrderDialog {...nifty} />)
    await userEvent.click(screen.getByRole('button', { name: 'Size by risk' }))
    await userEvent.type(screen.getByLabelText('Risk (₹)'), '4500')
    await userEvent.type(screen.getByLabelText('Stop price'), '120')
    expect(screen.getByText('For a buy, the stop goes below the price.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Set quantity/ })).toBeNull()
    expect(screen.getByText('Total qty: 150')).toBeInTheDocument()
  })

  it('opens with the sum last used for the segment on this phone', async () => {
    localStorage.setItem('sudarshan-ticket-risk', JSON.stringify({ fno: 3000 }))
    render(<PlaceOrderDialog {...nifty} />)
    await userEvent.click(screen.getByRole('button', { name: 'Size by risk' }))
    expect(screen.getByLabelText('Risk (₹)')).toHaveValue(3000)
  })
})
