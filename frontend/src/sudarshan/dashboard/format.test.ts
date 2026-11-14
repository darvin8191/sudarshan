import { describe, expect, it } from 'vitest'
import {
  contractWords,
  directionOf,
  formatAmount,
  formatPercent,
  formatPrice,
  formatRupees,
  toNumber,
} from './format'

describe('dashboard figures', () => {
  it('groups the Indian way', () => {
    expect(formatAmount(342180.4)).toBe('3,42,180.40')
    expect(formatAmount(12345678.9, 0)).toBe('1,23,45,679')
    expect(formatPrice(25874.3)).toBe('25,874.30')
  })

  it('signs rupees with a plus on request and a true minus always', () => {
    expect(formatRupees(48320.5)).toBe('₹48,320.50')
    expect(formatRupees(2450.75, { signed: true })).toBe('+₹2,450.75')
    expect(formatRupees(-840, { signed: true })).toBe('−₹840.00')
    expect(formatRupees(-840)).toBe('−₹840.00')
    expect(formatRupees(29780.4, { decimals: 0, signed: true })).toBe('+₹29,780')
  })

  it('gives zero, and anything that rounds to it, no sign and no direction', () => {
    expect(formatRupees(0, { signed: true })).toBe('₹0.00')
    expect(formatRupees(-0.004, { signed: true })).toBe('₹0.00')
    expect(directionOf(-0.004)).toBe('flat')
    expect(directionOf(0.006)).toBe('up')
    expect(directionOf(-0.006)).toBe('down')
    expect(formatPercent(0)).toBe('0.00%')
  })

  it('always signs a percentage that moved', () => {
    expect(formatPercent(0.6198)).toBe('+0.62%')
    expect(formatPercent(-3.1384)).toBe('−3.14%')
  })

  it('reads the API figures, which arrive as strings, and refuses junk', () => {
    expect(toNumber('48320.50')).toBe(48320.5)
    expect(toNumber('-2310.25')).toBe(-2310.25)
    expect(toNumber(12)).toBe(12)
    expect(toNumber('')).toBeNull()
    expect(toNumber(undefined)).toBeNull()
    expect(toNumber('abc')).toBeNull()
    expect(toNumber(Number.NaN)).toBeNull()
  })
})

// SUDARSHAN D4: the position rows name a contract as a broker's app does.
describe('contractWords', () => {
  it('names an option by its strike and side, and a future, with the expiry beside', () => {
    expect(contractWords('NIFTY17NOV2625900CE')).toEqual({
      name: 'NIFTY 25,900 CE',
      expiry: '17 Nov',
    })
    expect(contractWords('BANKNIFTY29DEC2658000PE')).toEqual({
      name: 'BANKNIFTY 58,000 PE',
      expiry: '29 Dec',
    })
    expect(contractWords('VEDL25APR24292.5CE')).toEqual({ name: 'VEDL 292.5 CE', expiry: '25 Apr' })
    expect(contractWords('NIFTY24NOV26FUT')).toEqual({ name: 'NIFTY FUT', expiry: '24 Nov' })
    expect(contractWords('NIFTYNXT5024NOV26FUT').name).toBe('NIFTYNXT50 FUT')
  })

  it('keeps anything else as it is, with no expiry', () => {
    expect(contractWords('RELIANCE')).toEqual({ name: 'RELIANCE', expiry: null })
    expect(contractWords('NIFTY')).toEqual({ name: 'NIFTY', expiry: null })
  })
})
