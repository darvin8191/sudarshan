import { describe, expect, it } from 'vitest'
import { dayText, monthOf, priceText, quantityText, rupeesText } from './format'

describe('how an entry’s figures read', () => {
  it('days and months', () => {
    expect(dayText(20261104)).toBe('4 Nov 2026')
    expect(dayText(20250312)).toBe('12 Mar 2025')
    expect(monthOf(20261104)).toEqual({ key: 202611, label: 'November 2026' })
  })
  it('money in Indian grouping with a true minus', () => {
    expect(rupeesText(1_428_250)).toBe('₹14,282.50')
    expect(rupeesText(-2_000_00)).toBe('−₹2,000.00')
    expect(rupeesText(0)).toBe('₹0.00')
    expect(rupeesText(12_34_56_789)).toBe('₹12,34,567.89')
  })
  it('units keep the decimals a fund or a metal has', () => {
    expect(quantityText(10_000_000)).toBe('10')
    expect(quantityText(12_345_600)).toBe('12.3456')
    expect(quantityText(1_234_567_000_000)).toBe('12,34,567')
    expect(quantityText(-5_000_000)).toBe('−5')
  })
  it('prices have two decimals, more only when they have them', () => {
    expect(priceText(14_282_500)).toBe('₹1,428.25')
    expect(priceText(10_000)).toBe('₹1.00')
    expect(priceText(12_345)).toBe('₹1.2345')
  })
})
