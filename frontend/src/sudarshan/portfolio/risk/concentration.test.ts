/**
 * The phone's concentration bars against the vectors the risk core runs
 * (sudarshan/portfolio/tests/vectors/risk.json: one copy, read from there): `concentration` is the
 * rule on holdings, `rows` the same from a tab's rows as the risk answer carries them. A change of
 * the rule in the service changes the vectors, and this file fails until the phone's copy follows.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { RiskConcentration } from '../types'
import { concentration, type Holding, holdingsOf, type RiskRow, TOP, two } from './concentration'

interface Case {
  id: string
  holdings: [string, string, number, string | null][]
  max_holding_pct: string
  max_sector_pct: string
  expect: RiskConcentration
}
const FILE = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', 'risk.json'),
    'utf8'
  )
) as { concentration: Case[]; rows: (Case & { rows: RiskRow[] })[] }
const held = (rows: Case['holdings']): Holding[] =>
  rows.map(([key, label, value_paise, sector]) => ({ key, label, value_paise, sector }))

describe('the concentration rule, as the service works it out', () => {
  it('has the cases to run', () => {
    expect(FILE.concentration.length).toBeGreaterThanOrEqual(5)
    expect(FILE.rows.length).toBeGreaterThanOrEqual(2)
  })

  it.each(FILE.concentration.map((c) => [c.id, c] as const))('%s', (_id, c) => {
    expect(concentration(held(c.holdings), c.max_holding_pct, c.max_sector_pct)).toEqual(c.expect)
  })

  it.each(FILE.rows.map((c) => [c.id, c] as const))('%s, from a tab’s rows', (_id, c) => {
    const found = holdingsOf(c.rows)
    expect(found.filter((each) => each.value_paise > 0)).toEqual(held(c.holdings))
    expect(concentration(found, c.max_holding_pct, c.max_sector_pct)).toEqual(c.expect)
  })
})

describe('the parts of the rule', () => {
  it('a sentence says a figure to two decimals, half up, and a whole one whole', () => {
    expect(two('22.5650')).toBe('22.57')
    expect(two('15.0000')).toBe('15')
    expect(two('0.0049')).toBe('0')
    expect(two('-0.0049')).toBe('0')
    expect(two('12.5')).toBe('12.50')
  })

  it('a limit is crossed only above it, on sums too large for a float', () => {
    // 1/3 of 3 x 10^15 paise: 33.3333% is above a limit of 33.3333 by less than a float can see.
    const third = 10 ** 15
    const big = held([
      ['a', 'A', third + 1, null],
      ['b', 'B', third, null],
      ['c', 'C', third - 1, null],
    ])
    const got = concentration(big, '33.3333', '100.0000')
    expect(got.holdings.map((bar) => bar.over_limit)).toEqual([true, true, true])
    const exact = concentration(
      held([
        ['a', 'A', 1500, null],
        ['b', 'B', 8500, null],
      ]),
      '15',
      '100'
    )
    expect(exact.holdings.find((bar) => bar.label === 'A')?.over_limit).toBe(false)
  })

  it('lists five, and a sixth only when it is over its limit; a tie goes by name', () => {
    const six = held(['F', 'E', 'D', 'C', 'B', 'A'].map((k) => [k, k, 100, null]))
    const eased = concentration(six, '50', '100')
    expect(eased.holdings.map((bar) => bar.label)).toEqual(['A', 'B', 'C', 'D', 'E'])
    expect(eased.holdings).toHaveLength(TOP)
    expect(concentration(six, '10', '100').holdings).toHaveLength(6)
  })
})
