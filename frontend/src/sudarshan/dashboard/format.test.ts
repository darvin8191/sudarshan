import { afterEach, describe, expect, it } from 'vitest'
import { makeFormatCurrency } from '@/lib/utils'
import {
  contractWords,
  directionOf,
  formatAmount,
  formatCompact,
  formatNumber,
  formatPercent,
  formatPrice,
  formatRupees,
  groupedNumber,
  toNumber,
} from './format'
import { numberGrouping, setNumberGrouping } from './numberStyle'

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

// SUDARSHAN M20.3 (GA10.2): the lakh/crore switch, through the one shared formatter.
describe('the number grouping switch', () => {
  afterEach(() => {
    setNumberGrouping('indian')
    localStorage.clear()
  })

  it('is Indian until the switch is thrown, and keeps nothing in storage for the default', () => {
    expect(numberGrouping()).toBe('indian')
    expect(formatRupees(1234567.89)).toBe('₹12,34,567.89')
    expect(localStorage.getItem('sudarshan-number-grouping')).toBeNull()
  })

  it('groups in threes when international is chosen, signs and the true minus unchanged', () => {
    setNumberGrouping('international')
    expect(localStorage.getItem('sudarshan-number-grouping')).toBe('international')
    expect(formatAmount(1234567.89)).toBe('1,234,567.89')
    expect(formatRupees(-1234567.89, { signed: true })).toBe('−₹1,234,567.89')
    expect(formatRupees(2450.75, { signed: true })).toBe('+₹2,450.75')
    expect(formatPrice(125874.3)).toBe('125,874.30')
    expect(formatPercent(-3.1384)).toBe('−3.14%')
    expect(groupedNumber().format(1234567)).toBe('1,234,567')
    expect(contractWords('NIFTY17NOV26125900CE').name).toBe('NIFTY 125,900 CE')

    setNumberGrouping('indian')
    expect(formatAmount(1234567.89)).toBe('12,34,567.89')
    expect(groupedNumber().format(1234567)).toBe('12,34,567')
    expect(localStorage.getItem('sudarshan-number-grouping')).toBeNull()
  })

  it('follows the switch in a format kept from before it, and in one-off figures', () => {
    const kept = groupedNumber({ maximumFractionDigits: 2 })
    const volume = groupedNumber({ notation: 'compact', maximumFractionDigits: 2 })
    expect(kept.format(1234567.891)).toBe('12,34,567.89')
    expect(volume.format(1234567)).toBe('12.35L')
    expect(formatNumber(2500000)).toBe('25,00,000')
    setNumberGrouping('international')
    expect(kept.format(1234567.891)).toBe('1,234,567.89')
    expect(volume.format(1234567)).toBe('1.23M')
    expect(formatNumber(2500000)).toBe('2,500,000')
  })

  it("groups upstream's rupee tables the same way (the books turned sideways)", () => {
    const rupees = makeFormatCurrency(null)
    expect(rupees(1234567.5)).toBe('₹12,34,567.50')
    setNumberGrouping('international')
    expect(rupees(1234567.5)).toBe('₹1,234,567.50')
    expect(makeFormatCurrency('deltaexchange')(1234567.5)).toBe('$1,234,567.50')
  })

  it.each([
    [812, '812', '812'],
    [1234, '1.2k', '1.2k'],
    [12_000, '12k', '12k'],
    [99_499, '99k', '99k'],
    [99_500, '1L', '100k'],
    [340_000, '3.4L', '340k'],
    [999_600, '10L', '1M'],
    [1_234_567, '12L', '1.2M'],
    [9_950_000, '1Cr', '10M'],
    [12_000_000, '1.2Cr', '12M'],
    [2_500_000_000, '250Cr', '2.5B'],
  ])('shortens %d to %s (Indian) and %s (international)', (value, indian, international) => {
    expect(formatCompact(value)).toBe(indian)
    expect(formatCompact(-value)).toBe(indian)
    setNumberGrouping('international')
    expect(formatCompact(value)).toBe(international)
  })

  it('never makes a short figure longer than four characters below a crore', () => {
    for (const grouping of ['indian', 'international'] as const) {
      setNumberGrouping(grouping)
      for (let value = 1; value < 9_900_000; value = Math.ceil(value * 1.07)) {
        expect(formatCompact(value).length).toBeLessThanOrEqual(4)
      }
    }
  })
})
