/**
 * The dock's figures follow Display settings' grouping (M20.3, v0.16.0 phone review M1), as its
 * header does through `makeFormatCurrency`: with international grouping on, the header read
 * "₹1,234,567.89" above rows reading "12,34,567.89". One grouping now, header and rows.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { makeFormatCurrency } from '@/lib/utils'
import { setNumberGrouping } from '@/sudarshan/dashboard/numberStyle'
import { fmt2, signed, signedQty } from './format'

afterEach(() => setNumberGrouping('indian'))

describe('the dock’s figures', () => {
  it('are grouped the Indian way unless Display settings say otherwise', () => {
    expect(fmt2(1234567.891)).toBe('12,34,567.89')
    expect(signed(-1234567.5)).toBe('-12,34,567.50')
    expect(signed(0)).toBe('0.00')
    expect(signedQty(150000)).toBe('+1,50,000')
    expect(makeFormatCurrency('upstox')(1234567.89)).toBe('₹12,34,567.89')
  })

  it('follow international grouping the moment it is chosen, as the header does', () => {
    setNumberGrouping('international')
    expect(fmt2(1234567.891)).toBe('1,234,567.89')
    expect(signed(1234567.5)).toBe('+1,234,567.50')
    expect(signedQty(-150000)).toBe('-150,000')
    expect(signedQty(0.5)).toBe('+0.5')
    expect(makeFormatCurrency('upstox')(1234567.89)).toBe('₹1,234,567.89')
    setNumberGrouping('indian')
    expect(fmt2(1234567.891)).toBe('12,34,567.89')
  })
})
