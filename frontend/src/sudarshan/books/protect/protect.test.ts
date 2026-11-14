import { describe, expect, it } from 'vitest'
import type { GttOrder } from '@/types/trading'
import {
  asks,
  checkQuantity,
  checkTrigger,
  protectable,
  protectedLine,
  protectRequest,
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
