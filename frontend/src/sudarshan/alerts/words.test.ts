/**
 * The alert form's arithmetic (M10.3) against the engine's own golden vectors: the snap group of
 * sudarshan/alerts/tests/market_vectors.json, which test_market_vectors.py runs on the Python side.
 * A typed level's edges in paise, where a level off the tick is met, and a feed price in paise
 * (float noise, zero and NaN), then the words the form shows from them.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  checksAtWords,
  levelEdges,
  levelFrom,
  paiseOf,
  paiseText,
  priceNote,
  tickEdges,
  tickOf,
} from './words'

interface LevelCase {
  id: string
  tick: string
  level: string
  up: string
  down: string
  checks_at: [string, string] | null
}
interface PriceCase {
  id: string
  price: number | string
  paise: number | null
  shown?: string
}

const VECTORS = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'alerts', 'tests', 'market_vectors.json'),
    'utf8'
  )
) as { snap: Array<LevelCase | PriceCase> }

const levels = VECTORS.snap.filter((each): each is LevelCase => 'level' in each)
const prices = VECTORS.snap.filter((each): each is PriceCase => 'price' in each)

/** JSON cannot carry NaN or Infinity, so the vectors write them as text. */
const number = (value: number | string) => (typeof value === 'string' ? Number(value) : value)

describe('the snap vectors', () => {
  it('reads every case of the group', () => {
    expect(levels.length).toBeGreaterThanOrEqual(8)
    expect(prices.length).toBeGreaterThanOrEqual(6)
  })

  it.each(levels)('$id: level $level on tick $tick', (vector) => {
    const edges = levelEdges(vector.level)
    expect(edges ? paiseText(edges.up) : null).toBe(vector.up)
    expect(edges ? paiseText(edges.down) : null).toBe(vector.down)
    const snapped = tickEdges(vector.level, vector.tick)
    expect(snapped ? snapped.map(paiseText) : null).toEqual(vector.checks_at)
  })

  it.each(prices)('$id: price $price', (vector) => {
    const paise = paiseOf(number(vector.price))
    expect(paise).toBe(vector.paise)
    if (vector.shown) expect(levelFrom(number(vector.price))).toBe(vector.shown)
  })
})

describe('what the form says about a level', () => {
  it('says where a level off the tick is met, by the check’s direction', () => {
    expect(checksAtWords('NSE', 'crosses_above', '100.03', '0.05')).toBe(
      'Checks at ₹100.05 (nearest tick)'
    )
    expect(checksAtWords('NSE', 'at_or_below', '100.03', '0.05')).toBe(
      'Checks at ₹100.00 (nearest tick)'
    )
    expect(checksAtWords('NSE', 'crosses', '1420.797', '0.05')).toBe(
      'Checks at ₹1,420.80 going up and ₹1,420.75 going down (nearest ticks)'
    )
  })

  it('reads a level on the tick back as it will be read, in points for an index', () => {
    expect(checksAtWords('NSE', 'crosses_above', '1,399.8', '0.05')).toBe('Checks at ₹1,399.80')
    expect(checksAtWords('NSE_INDEX', 'crosses_above', '25900', tickOf('NSE_INDEX', null))).toBe(
      'Checks at 25,900.00'
    )
  })

  it('says nothing for a level that is not a number above 0', () => {
    for (const level of ['', '0', 'abc', '1.2.3', '-5']) {
      expect(checksAtWords('NSE', 'crosses_above', level, '0.05')).toBeNull()
    }
  })

  it('warns when a crossing would wait for the price to come back first', () => {
    expect(priceNote('NIFTY', 'NSE_INDEX', 'crosses_above', '25900', 25903.15)).toEqual({
      warn: true,
      text: "NIFTY is already at or above 25,900.00. 'Crosses above' waits for it to fall below and come back; 'Is at or above' fires at once.",
    })
    expect(priceNote('SBIN', 'NSE', 'crosses_below', '100', 99.95)?.warn).toBe(true)
    expect(priceNote('SBIN', 'NSE', 'crosses_above', '100', 99.95)).toBeNull()
    expect(priceNote('SBIN', 'NSE', 'crosses', '100', 100.05)).toBeNull()
  })

  it('says a level check would fire at once', () => {
    expect(priceNote('SBIN', 'NSE', 'at_or_above', '100', 100.05)).toEqual({
      warn: false,
      text: 'SBIN is at or above ₹100.00 now, so this fires at once.',
    })
    expect(priceNote('SBIN', 'NSE', 'at_or_below', '100', 100.05)).toBeNull()
  })

  it('takes an index’s tick as hundredths and refuses a tick that is not a number', () => {
    expect(tickOf('NSE_INDEX', '0.05')).toBe('0.01')
    expect(tickOf('NFO', 0.05)).toBe('0.05')
    expect(tickOf('NSE', null)).toBeNull()
    expect(tickOf('NSE', 'x')).toBeNull()
  })
})
