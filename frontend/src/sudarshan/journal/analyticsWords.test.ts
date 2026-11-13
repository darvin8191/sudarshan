/** The analytics' words and short figures, typed by hand. */
import { describe, expect, it } from 'vitest'
import {
  compactPaise,
  digitsOf,
  monthWords,
  ratioWords,
  rWords,
  tileRupees,
} from './analyticsWords'

describe('a ratio with no number says why', () => {
  it.each([
    ['1.30', 'ok', '', '1.30'],
    ['60.00', 'ok', '%', '60.00%'],
    [null, 'no_trades', '%', 'No trades'],
    [null, 'no_losing_trades', '', 'No losing trades'],
    [null, 'no_variation', '', 'No variation'],
    [null, 'short', '', 'Too few days'],
    [null, 'no_capital', '', 'Set your trading capital'],
  ] as const)('%s in state %s', (value, state, suffix, words) => {
    expect(ratioWords(value, state, suffix)).toBe(words)
  })

  it('never shows a zero or infinity for a ratio the core did not work out', () => {
    expect(ratioWords(null, 'no_losing_trades')).not.toMatch(/0|∞|inf/i)
  })
})

describe('short figures', () => {
  it.each([
    [81250, '+813'],
    [-32500, '−325'],
    [0, '0'],
    [40, '0'],
    [123456, '+1.2k'],
    [-9_876_500, '−98.8k'],
    [25_000_000, '+2.5L'],
    [-2_500_000_000, '−2.5Cr'],
  ])('%i paise reads %s', (paise, text) => {
    expect(compactPaise(paise)).toBe(text)
  })

  it('shows whole rupees from a thousand up, paise below, with the sign and a true minus', () => {
    expect(tileRupees(39750)).toBe('+₹397.50')
    expect(tileRupees(-100000)).toBe('−₹1,000')
    expect(tileRupees(12_345_678)).toBe('+₹1,23,457')
    expect(tileRupees(100000, false)).toBe('₹1,000')
  })

  it('says R known for n of m, or what to do to get one', () => {
    expect(rWords({ r_known: 4, r_avg: '0.1325', trips: 9 })).toEqual({
      figure: '+0.13 R',
      note: 'R known for 4 of 9',
    })
    expect(rWords({ r_known: 2, r_avg: '-0.6500', trips: 2 }).figure).toBe('−0.65 R')
    expect(rWords({ r_known: 0, r_avg: null, trips: 5 })).toEqual({
      figure: 'No R yet',
      note: 'Add a risk to a trade to see R',
    })
    expect(rWords({ r_known: 0, r_avg: null, trips: 0 }).note).toBe('No trades')
  })

  it('reads keys by their digits and names a month', () => {
    expect(digitsOf('2026-11-13')).toBe('20261113')
    expect(digitsOf('202611')).toBe('202611')
    expect(monthWords(202611)).toBe('November 2026')
  })
})
