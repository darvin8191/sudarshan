import { describe, expect, it } from 'vitest'
import { atmOf, rollOf, ruleFor } from './strikePicker'
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

/**
 * Hand-typed from the server's rule (`sudarshan/watchlists/dynamic.py`, `pick_expiries`): the tabs are
 * the open expiries in order, each marked `month` when it is the last of its calendar month in the
 * contract list; `month` is the first open such expiry and `next_month` the next one.
 */
describe('ruleFor', () => {
  const tab = (date: string, label: string, month = false): Expiry => ({ date, label, month })
  // NIFTY in the week before the November monthly (24 Nov 2026): the server answers nearest 24 Nov,
  // next 1 Dec, month 24 Nov, next_month 29 Dec.
  const before: Expiry[] = [
    tab('2026-11-24', 'Nearest 24 Nov', true),
    tab('2026-12-01', '1 Dec'),
    tab('2026-12-08', '8 Dec'),
    tab('2026-12-15', '15 Dec'),
    tab('2026-12-22', '22 Dec'),
    tab('2026-12-29', '29 Dec (month)', true),
    tab('2027-01-26', '26 Jan (month)', true),
  ]
  // The same on 26 Nov, after the November monthly's close and before the end of November: the
  // server answers nearest 1 Dec, next 8 Dec, month 29 Dec, next_month 26 Jan (review M2).
  const after = before
    .slice(1)
    .map((each, i) => (i === 0 ? { ...each, label: 'Nearest 1 Dec' } : each))

  it('says the first tab is Nearest and the second Next', () => {
    expect(ruleFor(before, '2026-11-24')).toBe('nearest')
    expect(ruleFor(before, '2026-12-01')).toBe('next')
    expect(ruleFor(after, '2026-12-01')).toBe('nearest')
    expect(ruleFor(after, '2026-12-08')).toBe('next')
  })

  it('says the monthly after this month’s is Next month’s', () => {
    expect(ruleFor(before, '2026-12-29')).toBe('next_month')
  })

  it('from the monthly’s close to the end of its month, takes the next monthly for This month’s, as the server does', () => {
    // By the calendar, 29 Dec would be next month's and 26 Jan the month after: the server says not.
    expect(ruleFor(after, '2026-12-29')).toBe('month')
    expect(ruleFor(after, '2027-01-26')).toBe('next_month')
  })

  it('keeps a monthly in second place on This month’s, which stays on its date until its own close', () => {
    const weekly = [tab('2026-11-17', 'Nearest 17 Nov'), ...before]
    expect(ruleFor(weekly, '2026-11-17')).toBe('nearest')
    expect(ruleFor(weekly, '2026-11-24')).toBe('month')
    expect(ruleFor(weekly, '2026-12-01')).toBeNull()
    expect(ruleFor(weekly, '2026-12-29')).toBe('next_month')
  })

  it('has no rule for a weekly further out, a month after next, or a date not listed', () => {
    for (const date of ['2026-12-08', '2026-12-15', '2026-12-22', '2027-01-26', '2026-12-30'])
      expect(ruleFor(before, date)).toBeNull()
    for (const date of ['2026-12-15', '2026-12-22']) expect(ruleFor(after, date)).toBeNull()
  })

  it('says where each rule stands today, whose close moves it and where it goes (rollOf)', () => {
    // On 18 Nov: next is 1 Dec today, and leaves it for 8 Dec when 24 Nov expires (review M3).
    expect(rollOf(before, 'nearest')).toEqual({
      on: '2026-11-24',
      ends: '2026-11-24',
      to: '2026-12-01',
    })
    expect(rollOf(before, 'next')).toEqual({
      on: '2026-12-01',
      ends: '2026-11-24',
      to: '2026-12-08',
    })
    expect(rollOf(before, 'month')).toEqual({
      on: '2026-11-24',
      ends: '2026-11-24',
      to: '2026-12-29',
    })
    expect(rollOf(before, 'next_month')).toEqual({
      on: '2026-12-29',
      ends: '2026-11-24',
      to: '2027-01-26',
    })
    // On 26 Nov: this month's is 29 Dec, and next month's moves on 29 Dec to a month not listed yet.
    expect(rollOf(after, 'month')).toEqual({
      on: '2026-12-29',
      ends: '2026-12-29',
      to: '2027-01-26',
    })
    expect(rollOf(after, 'next_month')).toEqual({ on: '2027-01-26', ends: '2026-12-29', to: null })
    expect(rollOf([], 'nearest')).toBeNull()
    expect(rollOf(before.slice(0, 1), 'next')).toBeNull()
  })

  it('takes monthly-only expiries (a stock’s) in order: the third has no rule', () => {
    // The server: nearest and month 24 Nov, next and next_month 29 Dec; nothing names 26 Jan.
    const stock = [
      tab('2026-11-24', 'Nearest 24 Nov', true),
      tab('2026-12-29', '29 Dec (month)', true),
      tab('2027-01-26', '26 Jan (month)', true),
    ]
    expect(stock.map((each) => ruleFor(stock, each.date))).toEqual(['nearest', 'next', null])
  })
})
