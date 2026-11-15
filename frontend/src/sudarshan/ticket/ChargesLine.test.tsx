/**
 * The order ticket's charges line (B6: M5.10): the journal's estimate read 400 ms after the
 * ticket opens and after each change of side, quantity, price, type or product, one read at a
 * time and only for the order as it stands; "not read" for anything not read (the journal
 * switched off, the op unavailable, no answer), never ₹0; an exchange the card does not cover
 * said in words; a tap shows the parts, labelled as an estimate.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { OrderCharges } from '@/sudarshan/journal/types'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/sudarshan/journal/api', () => ({
  journalCall: (...args: unknown[]) => call(...args),
}))

import {
  CHARGES_SETTLE_MS,
  ChargesLine,
  type ChargesOrder,
  estimateKey,
  tradePrice,
} from './ChargesLine'

const ORDER: ChargesOrder = {
  symbol: 'NIFTY24NOV2625000CE',
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  pricetype: 'LIMIT',
  product: 'MIS',
  price: 131.2,
  triggerPrice: 0,
}

/** The service's answer for an order: its total grows with the quantity, as the card's does. */
function estimate(args: { quantity: number; action: string; exchange: string }): OrderCharges {
  const modelled = args.exchange !== 'BSE'
  const total = modelled ? 5178 + (args.quantity - 65) * 10 : 0
  return {
    segment: modelled ? 'options' : 'not_modelled',
    modelled,
    plan: 'plus',
    parts: modelled
      ? {
          brokerage_paise: 3000,
          stt_paise: 1279 + (args.quantity - 65) * 10,
          exchange_paise: 303,
          sebi_paise: 1,
          stamp_paise: 0,
          gst_paise: 595,
          total_paise: total,
        }
      : {
          brokerage_paise: 0,
          stt_paise: 0,
          exchange_paise: 0,
          sebi_paise: 0,
          stamp_paise: 0,
          gst_paise: 0,
          total_paise: 0,
        },
    turnover_paise: 852800,
    day: 20261116,
    rate_card: { version: '2026-11-14.2', from_day: 20260401 },
    left_out: args.action === 'SELL' && args.exchange === 'NSE' ? ['Depository charge'] : [],
  }
}

let held: Array<() => void> = []
let slow = false
let answer: (args: Record<string, unknown>) => unknown = (args) => ({
  enabled: true,
  data: estimate(args as never),
})

function reply(_name: string, args: Record<string, unknown>) {
  const value = answer(args)
  if (value instanceof Error) return Promise.reject(value)
  if (!slow) return Promise.resolve(value)
  return new Promise((resolve) => held.push(() => resolve(value)))
}

const line = () => screen.getByTestId('charges-line')

async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  held = []
  slow = false
  answer = (args) => ({ enabled: true, data: estimate(args as never) })
  call.mockReset().mockImplementation(reply)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the charges line', () => {
  it('reads the estimate 400 ms after the ticket opens, the order as the ticket holds it', async () => {
    render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(0)
    expect(call).not.toHaveBeenCalled()
    expect(line()).toHaveTextContent('Charges est. reading')
    await wait(CHARGES_SETTLE_MS)
    expect(line()).toHaveTextContent('Charges est. ₹51.78')
    expect(call).toHaveBeenCalledTimes(1)
    expect(call.mock.calls[0]).toEqual([
      'charges_estimate',
      {
        exchange: 'NFO',
        symbol: 'NIFTY24NOV2625000CE',
        action: 'SELL',
        quantity: 65,
        price_u: 1312000,
        product: 'MIS',
      },
    ])
  })

  it('reads once after a burst of changes, saying the old figure is updating meanwhile', async () => {
    const view = render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    view.rerender(<ChargesLine order={{ ...ORDER, quantity: 130 }} ltp={130.5} />)
    await wait(200)
    view.rerender(<ChargesLine order={{ ...ORDER, quantity: 195, product: 'NRML' }} ltp={130.5} />)
    await wait(200)
    view.rerender(
      <ChargesLine
        order={{ ...ORDER, quantity: 195, product: 'NRML', price: 131.25 }}
        ltp={130.5}
      />
    )
    expect(line()).toHaveTextContent('Charges est. ₹51.78 updating')
    await wait(CHARGES_SETTLE_MS - 1)
    expect(call).toHaveBeenCalledTimes(1)
    await wait(1)
    expect(call).toHaveBeenCalledTimes(2)
    expect(call.mock.calls[1][1]).toMatchObject({
      quantity: 195,
      product: 'NRML',
      price_u: 1312500,
    })
    expect(line()).toHaveTextContent('Charges est. ₹64.78')
    expect(line()).not.toHaveTextContent('updating')
  })

  it.each([
    ['side', { action: 'BUY' as const }],
    ['quantity', { quantity: 130 }],
    ['price', { price: 132 }],
    ['type', { pricetype: 'SL' as const, triggerPrice: 131 }],
    ['product', { product: 'NRML' }],
  ])('reads again when the %s changes', async (_what, change) => {
    const view = render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    view.rerender(<ChargesLine order={{ ...ORDER, ...change }} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    expect(call).toHaveBeenCalledTimes(2)
  })

  it('keeps one read in flight, then reads the order as it stands, not each step', async () => {
    slow = true
    const view = render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    expect(call).toHaveBeenCalledTimes(1)
    view.rerender(<ChargesLine order={{ ...ORDER, quantity: 130 }} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    view.rerender(<ChargesLine order={{ ...ORDER, quantity: 195 }} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    expect(call).toHaveBeenCalledTimes(1)
    await act(async () => held.shift()?.())
    await wait(0)
    expect(call).toHaveBeenCalledTimes(2)
    expect(call.mock.calls[1][1]).toMatchObject({ quantity: 195 })
    await act(async () => held.shift()?.())
    await wait(0)
    expect(line()).toHaveTextContent('Charges est. ₹64.78')
  })

  it('prices a market order at the last price when read, and a moving price asks nothing again', async () => {
    const market = { ...ORDER, pricetype: 'MARKET' as const, price: 0 }
    const view = render(<ChargesLine order={market} ltp={undefined} />)
    await wait(CHARGES_SETTLE_MS * 2)
    expect(call).not.toHaveBeenCalled()
    expect(line()).toHaveTextContent('Charges est. -')
    view.rerender(<ChargesLine order={market} ltp={130.55} />)
    await wait(CHARGES_SETTLE_MS)
    expect(call).toHaveBeenCalledTimes(1)
    expect(call.mock.calls[0][1]).toMatchObject({ price_u: 1305500 })
    view.rerender(<ChargesLine order={market} ltp={130.6} />)
    view.rerender(<ChargesLine order={market} ltp={130.65} />)
    await wait(CHARGES_SETTLE_MS * 3)
    expect(call).toHaveBeenCalledTimes(1)
    expect(line()).toHaveTextContent('Charges est. ₹51.78')
  })

  it('prices a stop-market order at its trigger, and reads nothing for an order not filled in', async () => {
    expect(tradePrice({ ...ORDER, pricetype: 'SL-M', triggerPrice: 128 }, 130)).toBe(128)
    expect(tradePrice({ ...ORDER, pricetype: 'SL', price: 129, triggerPrice: 128 }, 130)).toBe(129)
    expect(tradePrice({ ...ORDER, pricetype: 'LIMIT', price: 0 }, 130)).toBeNull()
    expect(estimateKey({ ...ORDER, quantity: 0 }, 130)).toBeNull()
    expect(estimateKey({ ...ORDER, quantity: 6.5 }, 130)).toBeNull()
    expect(estimateKey({ ...ORDER, symbol: '' }, 130)).toBeNull()
    render(<ChargesLine order={{ ...ORDER, quantity: 0 }} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS * 2)
    expect(call).not.toHaveBeenCalled()
    expect(line()).toHaveTextContent('Charges est. -')
  })

  it.each([
    ['the journal is switched off', () => ({ enabled: false, data: null, message: 'Off.' })],
    ['the op is not there or the service does not answer', () => new Error('not ready')],
  ])('says "not read", never ₹0, when %s', async (_why, given) => {
    answer = given
    render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    expect(line()).toHaveTextContent('Charges est. not read')
    expect(line()).not.toHaveTextContent('₹')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('reads again after a read that failed, once the order changes', async () => {
    answer = () => new Error('down')
    const view = render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    expect(line()).toHaveTextContent('not read')
    answer = (args) => ({ enabled: true, data: estimate(args as never) })
    view.rerender(<ChargesLine order={{ ...ORDER, quantity: 130 }} ltp={130.5} />)
    expect(line()).toHaveTextContent('Charges est. reading')
    await wait(CHARGES_SETTLE_MS)
    expect(line()).toHaveTextContent('Charges est. ₹58.28')
  })

  it('says an exchange the rate card does not cover in words, never as ₹0', async () => {
    render(<ChargesLine order={{ ...ORDER, exchange: 'BSE', symbol: 'SBIN' }} ltp={803} />)
    await wait(CHARGES_SETTLE_MS)
    expect(line()).toHaveTextContent('Charges est. not estimated for this exchange')
    expect(line()).not.toHaveTextContent('₹')
  })

  it('shows the parts on a tap, labelled as an estimate, with what it leaves out', async () => {
    answer = (args) => ({ enabled: true, data: estimate({ ...(args as never), exchange: 'NSE' }) })
    render(<ChargesLine order={{ ...ORDER, exchange: 'NSE', symbol: 'SBIN' }} ltp={803} />)
    await wait(CHARGES_SETTLE_MS)
    const button = screen.getByRole('button', { name: /Charges est\. ₹51\.78/ })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('list')).toBeNull()
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    const parts = screen.getByRole('list', { name: 'Estimated charges, part by part' })
    expect([...parts.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'Brokerage₹30.00',
      'STT₹12.79',
      'Exchange charges₹3.03',
      'SEBI fee₹0.01',
      'Stamp duty₹0.00',
      'GST₹5.95',
    ])
    expect(line()).toHaveTextContent(
      'An estimate from the journal’s rate card for one executed order at ₹131.20 a unit, on the Upstox Plus plan.'
    )
    expect(line()).toHaveTextContent('Not in this estimate: Depository charge.')
    fireEvent.click(button)
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('gives the tap a target a thumb can hit', async () => {
    render(<ChargesLine order={ORDER} ltp={130.5} />)
    await wait(CHARGES_SETTLE_MS)
    expect(screen.getByRole('button').className).toContain('min-h-12')
  })
})
