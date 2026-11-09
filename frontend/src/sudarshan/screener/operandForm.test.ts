import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { GROUP_TITLES, INDICATOR_BY_ID } from './catalog'
import {
  AGO_WORDS,
  ITEMS,
  itemsOf,
  NUMBER_WORDS,
  OPERAND_TABS,
  offsetRefusal,
  operandProblems,
  paramError,
  pickOf,
  readsCandles,
  searchItems,
  starting,
  tabOfPick,
  timeframesOf,
} from './operandForm'
import { groupOf, whyNoClause, whyNoGroup } from './tree'
import type { Clause } from './types'

/** The service's golden vectors for spec.check: the words are the contract the builder reads. */
const VECTORS = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'screener', 'tests', 'vectors', 'spec.json'),
    'utf8'
  )
) as { cases: Array<{ id: string; error?: { field: string; message: string } }> }
const said = (id: string) => VECTORS.cases.find((each) => each.id === id)?.error?.message

describe('the operand sheet offers what the service can read', () => {
  it('has the five tabs, each with something to pick', () => {
    expect(OPERAND_TABS.map((tab) => tab.label)).toEqual([
      'Price',
      'Indicators',
      'Volume',
      'OI & options',
      'Number',
    ])
    for (const tab of OPERAND_TABS) expect(itemsOf(tab.id).length).toBeGreaterThan(0)
  })

  it('lists every field, indicator and option once', () => {
    const keys = ITEMS.map((item) => item.key)
    expect(new Set(keys).size).toBe(keys.length)
    expect(keys).toContain('ind:macd')
    expect(keys).toContain('opt:pcr')
    expect(keys).toContain('oi:buildup')
    expect(keys).toContain('field:prev_day_high')
  })

  it('searches across the tabs and finds nothing for an empty search', () => {
    expect(searchItems('rsi').map((item) => item.key)).toContain('ind:rsi')
    expect(searchItems('pcr')[0].key).toBe('opt:pcr')
    expect(searchItems('  ')).toEqual([])
    expect(searchItems('qqqq')).toEqual([])
  })

  it('starts an operand from an item on the screen own candles, parameters at their defaults', () => {
    expect(starting({ kind: 'ind', id: 'ema' }, 15)).toEqual({
      ind: 'ema',
      p: { length: 20, source: 'close' },
      tf: 15,
    })
    expect(starting({ kind: 'field', id: 'close' }, 5)).toEqual({ field: 'close', tf: 5 })
    expect(starting({ kind: 'field', id: 'prev_close' }, 5)).toEqual({ field: 'prev_close' })
    expect(starting({ kind: 'oi', what: 'buildup' }, 15)).toEqual({
      oi: 'buildup',
      window: { kind: 'day' },
    })
    expect(starting({ kind: 'opt', id: 'pcr' }, 15)).toEqual({ opt: 'pcr', underlying: 'NIFTY' })
    expect(starting({ kind: 'opt', id: 'vix_pct' }, 15)).toEqual({ opt: 'vix_pct' })
    expect(starting({ kind: 'opt', id: 'iv_change' }, 15)).toEqual({
      opt: 'iv_change',
      underlying: 'NIFTY',
      tf: 15,
    })
  })

  it('moves an indicator that cannot read the screen candles to one it can', () => {
    expect(timeframesOf({ kind: 'ind', id: 'pivot' })).toEqual(['D'])
    expect(starting({ kind: 'ind', id: 'pivot' }, 15)).toMatchObject({ tf: 'D' })
    expect(timeframesOf({ kind: 'ind', id: 'orb_high' })).toEqual([1, 3, 5, 15, 30, 60])
  })

  it('knows what an operand is, which tab it is on and whether it reads candles', () => {
    expect(pickOf({ ind: 'rsi', tf: 15 })).toEqual({ kind: 'ind', id: 'rsi' })
    expect(tabOfPick({ kind: 'ind', id: 'obv' })).toBe('volume')
    expect(tabOfPick({ kind: 'field', id: 'oi' })).toBe('oi')
    expect(tabOfPick({ kind: 'num' })).toBe('number')
    expect(readsCandles({ kind: 'field', id: 'prev_close' })).toBe(false)
    expect(readsCandles({ kind: 'ind', id: 'rsi' })).toBe(true)
  })
})

describe('the words are the service words (spec.json)', () => {
  const rsi = INDICATOR_BY_ID.get('rsi')
  const supertrend = INDICATOR_BY_ID.get('supertrend')

  it('says what a parameter out of range must be (SE40, SE52)', () => {
    const length = rsi?.params[0]
    const factor = supertrend?.params[1]
    expect(rsi && length && paramError(rsi, length, '1')).toBe(said('SE40'))
    expect(supertrend && factor && paramError(supertrend, factor, '11')).toBe(said('SE52'))
    expect(rsi && length && paramError(rsi, length, '14')).toBeNull()
    expect(rsi && length && paramError(rsi, length, '14.5')).toBe(said('SE40'))
  })

  it('refuses a minute-candle offset on a day or week candle in the service words (SE4, SE4b)', () => {
    expect(offsetRefusal('first_today', 'D')).toBe(said('SE4'))
    expect(offsetRefusal('last_prev_day', 'W')).toBe(said('SE4b'))
    expect(offsetRefusal('first_today', 15)).toBeNull()
    expect(offsetRefusal(0, 'D')).toBeNull()
  })

  it('uses the service words for a typed number, the look-back, the depth and the cap (SE37, SE35, SE5, SE8)', () => {
    expect(NUMBER_WORDS).toBe(said('SE37'))
    expect(AGO_WORDS).toBe(said('SE35'))
    expect(whyNoGroup(groupOf('all', []), [0, 0])).toBe(said('SE5'))
    const full = groupOf(
      'all',
      Array.from(
        { length: 20 },
        (_, i): Clause => ({
          id: `c${i}`,
          on: true,
          left: { num: '1' },
          op: 'gt',
          right: { num: '2' },
        })
      )
    )
    expect(whyNoClause(full)).toBe(said('SE8'))
  })

  it('names the group kinds as the design does', () => {
    expect(GROUP_TITLES).toEqual({
      all: 'Match all of these',
      any: 'Match any of these',
      none: 'Match none of these',
    })
  })

  it('finds an indicator parameter and a look-back that cannot be used', () => {
    expect([...operandProblems({ ind: 'rsi', p: { length: 1 }, tf: 15 }).keys()]).toEqual([
      'length',
    ])
    expect([...operandProblems({ field: 'close', tf: 15, at: { ago: 101 } }).keys()]).toEqual([
      'at',
    ])
    expect(operandProblems({ ind: 'rsi', p: { length: 14 }, tf: 15, at: { ago: 3 } }).size).toBe(0)
  })
})
