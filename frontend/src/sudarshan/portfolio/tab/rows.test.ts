/** The holdings list: figures, weights, average cost, price words, sort and movers (design 4.5). */
import { describe, expect, it } from 'vitest'
import { row } from './fixtures'
import { averageCostU, listRows, moversOf, priceWords, sortRows } from './rows'

describe('average cost', () => {
  it('is cost per unit as price_u', () => {
    expect(averageCostU(90_000, 10_000_000)).toBe(900_000) // 900 rupees for 10 units: 90 a unit
    expect(averageCostU(0, 10_000_000)).toBeNull() // a bonus lot has no cost
    expect(averageCostU(90_000, 0)).toBeNull()
  })
})

describe('price words', () => {
  it('Real-time only for a row a tick moved', () => {
    expect(priceWords(row(1, 'A'), true).words).toBe('Real-time')
    expect(priceWords(row(1, 'A'), false).words).toBe('Price of 10:41:05')
  })
  it('no price is a phrase, never a number', () => {
    expect(priceWords(row(1, 'A', { price: null, day: null }), false).words).toBe('No price yet')
  })
  it('an old NAV or manual value keeps its words and is marked old', () => {
    const old = row(2, 'F', {
      kind: 'mf',
      fresh: { state: 'old', words: 'NAV of 21 Oct; AMFI has not published a newer one.' },
    })
    expect(priceWords(old, false)).toEqual({
      words: 'NAV of 21 Oct; AMFI has not published a newer one.',
      old: true,
    })
    const manual = row(3, 'Flat', {
      kind: 'manual',
      fresh: { state: 'fresh', words: 'Value from 12 Sep · 45 days old' },
    })
    expect(priceWords(manual, false).old).toBe(false)
  })
})

describe('the list', () => {
  const rows = [
    row(1, 'Alpha', { price: 1_100_000 }), // +10 on 99 prev... value 1,100
    row(2, 'Beta', { price: 900_000 }), // down
    row(3, 'Gamma', { price: null, prev: null }), // no price
    row(4, 'Delta', { prev: null }), // no previous close
  ]
  const list = listRows(rows, [false, false, false, false], 4_000_000)

  it('skips a row nobody holds and keeps the rest', () => {
    const gone = row(5, 'Gone', { qty: 0 })
    gone.lines[0].qty_u = 0
    expect(listRows([gone], [false], 1)).toHaveLength(0)
    expect(list).toHaveLength(4)
  })

  it('a missing price or close is a state, never a zero', () => {
    expect(list[2].figures.priced).toBe(false)
    expect(list[2].figures.gain_pct).toBeNull()
    expect(list[3].figures.today.state).toBe('unknown')
    expect(list[3].figures.today.paise).toBeNull()
  })

  it('sorts by value or name, and unknown figures last', () => {
    expect(sortRows(list, 'value').map((r) => r.row.asset.name)[0]).toBe('Alpha')
    expect(sortRows(list, 'name').map((r) => r.row.asset.name)).toEqual([
      'Alpha',
      'Beta',
      'Delta',
      'Gamma',
    ])
    const byToday = sortRows(list, 'today_pct').map((r) => r.row.asset.name)
    expect(byToday.slice(-1)[0]).not.toBe('Alpha')
    expect(byToday[0]).toBe('Alpha')
  })

  it('movers: best and worst by percent, never a holding with no Today', () => {
    const movers = moversOf(list)
    expect(movers.best.map((r) => r.row.asset.name)).toEqual(['Alpha'])
    expect(movers.worst.map((r) => r.row.asset.name)).toEqual(['Beta'])
    expect(movers.contributors.map((r) => r.row.asset.name)).toEqual(['Alpha', 'Beta'])
    const all = [...movers.best, ...movers.worst, ...movers.contributors].map(
      (r) => r.row.asset.name
    )
    expect(all).not.toContain('Gamma')
    expect(all).not.toContain('Delta')
  })
})
