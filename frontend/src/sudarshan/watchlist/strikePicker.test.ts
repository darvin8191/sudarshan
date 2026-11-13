import { describe, expect, it } from 'vitest'
import { atmOf, ruleFor } from './strikePicker'
import type { Expiry } from './v2/types'

describe('atmOf', () => {
  const strikes = [25800, 25850, 25900, 25950, 26000]
  it('is the nearest strike, a tie to the lower', () => {
    expect(atmOf(25900, strikes)).toBe(25900)
    expect(atmOf(25924.95, strikes)).toBe(25900)
    expect(atmOf(25925, strikes)).toBe(25900) // the midpoint belongs to the lower strike
    expect(atmOf(25925.05, strikes)).toBe(25950)
    expect(atmOf(30000, strikes)).toBe(26000)
    expect(atmOf(100, strikes)).toBe(25800)
  })
  it('is null without a price or without strikes, and does not need them sorted', () => {
    expect(atmOf(null, strikes)).toBeNull()
    expect(atmOf(0, strikes)).toBeNull()
    expect(atmOf(25900, [])).toBeNull()
    expect(atmOf(25925, [25950, 25900, 25800])).toBe(25900)
  })
})

describe('ruleFor', () => {
  const tabs: Expiry[] = [
    { date: '2026-11-24', label: 'Nearest 24 Nov', month: false },
    { date: '2026-12-01', label: '1 Dec', month: false },
    { date: '2026-12-08', label: '8 Dec', month: false },
    { date: '2026-12-29', label: '29 Dec (month)', month: true },
    { date: '2027-01-26', label: '26 Jan (month)', month: true },
  ]
  const now = Date.UTC(2026, 10, 13, 4, 42)
  it('says the first tab is Nearest and the second Next', () => {
    expect(ruleFor(tabs, '2026-11-24', now)).toBe('nearest')
    expect(ruleFor(tabs, '2026-12-01', now)).toBe('next')
  })
  it('says a monthly expiry in the next calendar month is Next month’s', () => {
    expect(ruleFor(tabs, '2026-12-29', now)).toBe('next_month')
  })
  it('says a monthly expiry in this calendar month is This month’s', () => {
    const later = [tabs[1], tabs[2], { date: '2026-12-15', label: '15 Dec', month: false }, tabs[3]]
    expect(ruleFor(later, '2026-12-29', Date.UTC(2026, 11, 2, 4, 0))).toBe('month')
  })
  it('has no rule for a weekly further out, a month after next, or a date not listed', () => {
    expect(ruleFor(tabs, '2026-12-08', now)).toBeNull()
    expect(ruleFor(tabs, '2027-01-26', now)).toBeNull()
    expect(ruleFor(tabs, '2026-12-30', now)).toBeNull()
  })
  it('reads the month in IST, not UTC', () => {
    const edge = [tabs[0], tabs[1], { date: '2026-12-29', label: '29 Dec (month)', month: true }]
    // 30 Nov 20:00 UTC is 1 Dec 01:30 IST: this month is December.
    expect(ruleFor(edge, '2026-12-29', Date.UTC(2026, 10, 30, 20, 0))).toBe('month')
  })
})
