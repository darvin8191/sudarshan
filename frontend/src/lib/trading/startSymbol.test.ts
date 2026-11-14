/** What a new pane of a grid opens with (SCR-P item 9). */
import { describe, expect, it } from 'vitest'
import { panesForLayout, parseSymbolKey, seedPane } from './startSymbol'

describe('parseSymbolKey', () => {
  it('reads the exchange and the symbol of a pane’s key', () => {
    expect(parseSymbolKey('NSE:TESTCO')).toEqual({ exchange: 'NSE', symbol: 'TESTCO' })
    expect(parseSymbolKey('NFO:TEST17NOV2625900CE')).toEqual({
      exchange: 'NFO',
      symbol: 'TEST17NOV2625900CE',
    })
  })

  it('reads a computed chart, which has no exchange, with its legs’ own colons left whole', () => {
    expect(parseSymbolKey(':AAA/BBB')).toEqual({ exchange: '', symbol: 'AAA/BBB' })
    expect(parseSymbolKey(':NSE:AAA/NSE:BBB')).toEqual({ exchange: '', symbol: 'NSE:AAA/NSE:BBB' })
  })

  it('reads nothing from a pane that has no instrument yet', () => {
    for (const key of [null, undefined, '', 'TESTCO', ':', 'NSE:']) {
      expect(parseSymbolKey(key)).toBeNull()
    }
  })
})

const pane = (id: string, symbol: string) => ({ id, symbol, interval: '5m' })

describe('the panes of the next layout', () => {
  const two = [pane('p0', 'AAA'), pane('p1', 'BBB')]

  it('gives each new pane the focused pane’s chart, not the first pane’s', () => {
    // On the old code the two new panes of a grid of four were the first pane again (AAA).
    expect(panesForLayout(two, 'p1', 4)).toEqual([
      pane('p0', 'AAA'),
      pane('p1', 'BBB'),
      pane('p2', 'BBB'),
      pane('p3', 'BBB'),
    ])
  })

  it('keeps every pane that stays as it is', () => {
    expect(panesForLayout(two, 'p0', 2)).toEqual(two)
    expect(panesForLayout(two, 'p1', 1)).toEqual([pane('p0', 'AAA')])
  })

  it('opens a grid from one chart with that chart in every pane', () => {
    expect(panesForLayout([pane('p0', 'AAA')], 'p0', 2).map((each) => each.symbol)).toEqual([
      'AAA',
      'AAA',
    ])
  })

  it('takes after the first pane when the focus names none of them', () => {
    expect(seedPane(two, 'p9')).toBe(two[0])
    expect(panesForLayout(two, 'p9', 3)[2]).toEqual(pane('p2', 'AAA'))
  })

  it('names the panes p0 onward and leaves the given ones untouched', () => {
    const made = panesForLayout(two, 'p1', 3)
    expect(made.map((each) => each.id)).toEqual(['p0', 'p1', 'p2'])
    expect(two).toEqual([pane('p0', 'AAA'), pane('p1', 'BBB')])
  })
})
