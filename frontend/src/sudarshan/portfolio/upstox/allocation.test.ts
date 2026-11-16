/**
 * The phone's bars of the Upstox book against the vectors the allocation core runs
 * (sudarshan/portfolio/tests/vectors/upstox_parity.json: one copy, read from there). A change of
 * the rule in the service changes the vectors, and this file fails until the phone's copy follows.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { AllocationRow, AssetClass } from '../types'
import { BOOK_BY, type BookLine, bookBars, linesOf, unclassified } from './allocation'

interface Case {
  id: string
  lines: [string, number, AssetClass | null, string | null][]
  expect: Record<'class' | 'sector', AllocationRow[]>
}
const FILE = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', 'upstox_parity.json'),
    'utf8'
  )
) as { allocation: Case[] }
const lines = (c: Case): BookLine[] =>
  c.lines.map(([key, value_paise, asset_class, sector]) => ({
    key,
    value_paise,
    asset_class,
    sector,
  }))

describe('the bars of the Upstox book, as the service works them out', () => {
  it('has the cases to run', () => {
    expect(FILE.allocation.length).toBeGreaterThanOrEqual(3)
  })

  for (const c of FILE.allocation) {
    it(c.id, () => {
      for (const by of BOOK_BY) {
        expect(bookBars(by, lines(c))).toEqual(c.expect[by])
        expect(bookBars(by, [...lines(c)].reverse())).toEqual(c.expect[by])
      }
    })
  }

  it('gives a holding the service has not classified to the bar that says so, never a guess', () => {
    const rows = [{ instrument_key: 'NSE_EQ|A' }, { instrument_key: 'NSE_EQ|B' }]
    const made = linesOf(
      rows,
      [700, 300],
      [{ instrument_key: 'NSE_EQ|A', asset_class: 'equity', sector: '' }]
    )
    expect(made[0]).toEqual({
      key: 'NSE_EQ|A',
      value_paise: 700,
      asset_class: 'equity',
      sector: null,
    })
    expect(made[1]).toEqual({ key: 'NSE_EQ|B', value_paise: 300, asset_class: null, sector: null })
    expect(unclassified(made)).toBe(2)
    expect(bookBars('class', made).map((b) => [b.label, b.pct])).toEqual([
      ['Equity', '70.0000'],
      ['Not classified', '30.0000'],
    ])
    expect(bookBars('sector', made)).toEqual([
      { key: '', label: 'No sector set', value_paise: 1000, pct: '100.0000', holdings: 2 },
    ])
  })
})
