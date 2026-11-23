import { describe, expect, it } from 'vitest'
import type { GttOrder } from '@/types/trading'
import {
  asks,
  checkQuantity,
  checkTrigger,
  gttToast,
  protectable,
  protectedLine,
  protectRemovedWords,
  protectRequest,
  protectSetWords,
  protectShown,
  protectsOf,
  refusal,
  sellQuestion,
} from './protect'
import vectors from './protect_vectors.json'

const gtt = (over: Partial<GttOrder> = {}, leg: Record<string, unknown> = {}): GttOrder => ({
  trigger_id: 'GTT-1',
  trigger_type: 'single',
  status: 'active',
  symbol: 'RELIANCE',
  exchange: 'NSE',
  trigger_prices: [1240],
  last_price: 1265,
  legs: [{ action: 'SELL', quantity: 50, price: 1240, pricetype: 'LIMIT', product: 'CNC', ...leg }],
  ...over,
})
const HELD = { symbol: 'RELIANCE', exchange: 'NSE', quantity: 50, product: 'CNC' }

describe('Protect, the pure part', () => {
  it.each(vectors.requests)('request: $name', (v) => {
    const check = checkTrigger(v.trigger, v.last)
    expect(check).toMatchObject(v.check)
    expect(checkQuantity(v.quantity, v.holding.quantity)).toBeNull()
    if (!check.ok) throw new Error('refused')
    const request = protectRequest('KEY', v.holding, v.quantity, v.trigger, check.side)
    expect(request).toEqual(v.request)
    // never two legs, never a buy, never another product, exactly one trigger
    expect(request.trigger_type).toBe('SINGLE')
    expect(request.action).toBe('SELL')
    expect(request.product).toBe('CNC')
    expect([request.triggerprice_sl, request.triggerprice_tg].filter((p) => p > 0)).toHaveLength(1)
    expect(Object.keys(request)).not.toContain('stoploss')
    expect(Object.keys(request)).not.toContain('target')
  })

  it.each(vectors.refused)('refused: $name', (v) => {
    const check = checkTrigger(v.trigger, v.last)
    expect(check.ok).toBe(false)
    if (!check.ok) expect(check.sentence).toContain(v.says)
  })

  it.each(vectors.holdings)('offered on: $name', (v) => {
    expect(protectable(v.holding)).toBe(v.protectable)
    // mutation 15: never on an F&O position
    if (!v.protectable) expect(refusal(v.holding, [])).toMatch(/shares you hold for delivery/)
  })

  it('reads a holding’s working sell GTTs from the GTT book, and only those', () => {
    const book = [
      gtt(),
      gtt({ trigger_id: 'GTT-2', status: 'cancelled' }),
      gtt({ trigger_id: 'GTT-3', symbol: 'SBIN' }),
      gtt({ trigger_id: 'GTT-4', exchange: 'BSE' }),
      gtt({ trigger_id: 'GTT-5' }, { action: 'BUY' }),
      gtt({ trigger_id: 'GTT-6', status: 'triggered' }),
    ]
    expect(protectsOf(HELD, book)).toEqual([{ triggerId: 'GTT-1', quantity: 50, trigger: 1240 }])
    expect(protectedLine(protectsOf(HELD, book)[0])).toBe('Protected: sell 50 at 1,240.00')
  })

  it('refuses a second Protect on one holding (mutation 15)', () => {
    expect(refusal(HELD, [])).toBeNull()
    expect(refusal(HELD, protectsOf(HELD, [gtt()]))).toBe(
      'RELIANCE already has a Protect order. Change it or remove it first.'
    )
  })

  it('takes whole shares, at least one and no more than held', () => {
    expect(checkQuantity(50, 50)).toBeNull()
    expect(checkQuantity(51, 50)).toBe('You hold 50. Protect cannot sell more than that.')
    for (const bad of [0, -1, 1.5, null, Number.NaN])
      expect(checkQuantity(bad, 50)).toMatch(/whole/)
  })

  it('shows only where its stage says', () => {
    expect(protectShown('off', true)).toBe(false)
    expect(protectShown('sandbox', true)).toBe(true)
    expect(protectShown('sandbox', false)).toBe(false)
    expect(protectShown('on', false)).toBe(true)
    for (const other of [undefined, null, '', 'true', 1]) {
      expect(protectShown(other, true)).toBe(false)
    }
  })

  it('words the question and the sell warning', () => {
    expect(asks('RELIANCE', 50, 1240, 'falls')).toBe(
      'Sell 50 RELIANCE if the price falls to 1,240.00?'
    )
    expect(sellQuestion({ triggerId: 'x', quantity: 50, trigger: 1240 })).toBe(
      'A Protect order for 50 shares is active. Remove it too?'
    )
  })
})

/**
 * Test day TD6: after Set Protect the toast said "SELL Order Placed for Symbol: ENGINERSIN, Qty: 2",
 * which reads like a sale sent now. A Protect says what it will do; any other GTT says it waits.
 */
describe('what a Protect and a GTT say once set (TD6)', () => {
  const ENGINERSIN = { symbol: 'ENGINERSIN', quantity: 2, trigger: 270, side: 'falls' } as const

  it('a Protect set, changed or removed, in Live and in the sandbox', () => {
    expect(protectSetWords('set', ENGINERSIN, true)).toBe(
      'Protect set: ENGINERSIN sells 2 in the sandbox if the price falls to 270.00.'
    )
    expect(protectSetWords('set', ENGINERSIN, false)).toBe(
      'Protect set: ENGINERSIN sells 2 if the price falls to 270.00.'
    )
    expect(protectSetWords('changed', { ...ENGINERSIN, trigger: 265 }, true)).toBe(
      'Protect changed: ENGINERSIN sells 2 in the sandbox if the price falls to 265.00.'
    )
    expect(
      protectSetWords(
        'set',
        { symbol: 'RELIANCE', quantity: 1500, trigger: 1640.5, side: 'rises' },
        false
      )
    ).toBe('Protect set: RELIANCE sells 1,500 if the price rises to 1,640.50.')
    const working = { triggerId: 'GTT-1', quantity: 2, trigger: 265 }
    expect(protectRemovedWords('ENGINERSIN', working, true)).toBe(
      'Protect removed in the sandbox: the order to sell 2 ENGINERSIN at 265.00 is cancelled. ENGINERSIN is not protected now.'
    )
    expect(protectRemovedWords('ENGINERSIN', working, false)).toBe(
      'Protect removed: the order to sell 2 ENGINERSIN at 265.00 is cancelled. ENGINERSIN is not protected now.'
    )
  })

  it("the sandbox's event for a Protect: its words, and the toast the Protect sheet shares", () => {
    const { apikey: _key, ...sent } = protectRequest(
      'k',
      { symbol: 'ENGINERSIN', exchange: 'NSE' },
      2,
      270,
      'falls'
    )
    expect(gttToast(sent)).toEqual({
      text: 'Protect set: ENGINERSIN sells 2 in the sandbox if the price falls to 270.00.',
      id: 'protect:NSE:ENGINERSIN',
    })
    expect(gttToast({ ...sent, trigger_id: 'GTT-1', price: 265, triggerprice_sl: 265 })).toEqual({
      text: 'Protect changed: ENGINERSIN sells 2 in the sandbox if the price falls to 265.00.',
      id: 'protect:NSE:ENGINERSIN',
    })
  })

  it('any other GTT set or changed waits for its price, and never reads as an order placed', () => {
    const single = {
      strategy: 'Python',
      trigger_type: 'SINGLE',
      exchange: 'NSE',
      symbol: 'SBIN',
      action: 'BUY',
      product: 'CNC',
      quantity: 5,
      pricetype: 'LIMIT',
      price: 790,
      triggerprice_sl: 0,
      triggerprice_tg: 790,
    }
    expect(gttToast(single)).toEqual({
      text: 'GTT set in the sandbox: BUY 5 SBIN when the price reaches 790.00. No order is placed until then.',
    })
    expect(
      gttToast({
        ...single,
        trigger_type: 'oco',
        action: 'SELL',
        triggerprice_sl: 760,
        triggerprice_tg: 830,
        trigger_id: '9',
      })
    ).toEqual({
      text: 'GTT changed in the sandbox: SELL 5 SBIN when the price reaches 760.00 or 830.00. No order is placed until then.',
    })
    // A Protect's tag on a buy is not a Protect.
    expect(gttToast({ ...single, strategy: 'Protect' })?.id).toBeUndefined()
    for (const said of [gttToast(single)?.text]) expect(said).not.toMatch(/Order Placed/i)
  })

  it('says nothing of its own for a GTT cancelled or fired, or an order', () => {
    expect(gttToast({ trigger_id: 'GTT-1' })).toBeNull()
    expect(gttToast({ api_type: 'gtttriggered', trigger_id: 'GTT-1', symbol: 'SBIN' })).toBeNull()
    expect(
      gttToast({ api_type: 'placeorder', symbol: 'SBIN', action: 'BUY', quantity: '1' })
    ).toBeNull()
  })
})
