/** A computed chart's arithmetic (M4.13, C11): the fold of two legs' candles and the combined tick. */
import type { Bar, DataFeed } from 'openalgo-charts'
import { beforeAll, describe, expect, it } from 'vitest'
import {
  ComputedFeed,
  type ComputedSpec,
  computedKey,
  type Evaluator,
  foldBars as fold,
  LegPrices,
  specProblem,
  transformTier,
} from './computed'

const bar = (time: number, open: number, high: number, low: number, close: number): Bar => ({
  time,
  open,
  high,
  low,
  close,
  volume: 100,
})
const CE = { symbol: 'NIFTY17NOV2625900CE', exchange: 'NFO' }
const PE = { symbol: 'NIFTY17NOV2625900PE', exchange: 'NFO' }
const spec: ComputedSpec = { legs: [CE, PE], op: 'sum' }

let engine: Evaluator
beforeAll(async () => {
  engine = await transformTier()
})
const foldBars = (legs: ReadonlyArray<readonly Bar[]>) => fold(legs, engine)

describe('foldBars', () => {
  it('keeps only the buckets both legs have, and sums their opens and closes', () => {
    const folded = foldBars([
      [bar(60, 100, 110, 95, 104), bar(120, 104, 106, 101, 102), bar(240, 90, 91, 89, 90)],
      [bar(120, 80, 90, 78, 85), bar(180, 85, 86, 84, 85), bar(240, 70, 72, 69, 71)],
    ])
    expect(folded.map((each) => each.time)).toEqual([120, 240])
    expect(folded[0]).toMatchObject({ open: 184, close: 187 })
    expect(folded[1]).toMatchObject({ open: 160, close: 161 })
  })

  it("never claims a range from the legs' own highs and lows, which need not have met", () => {
    const [only] = foldBars([[bar(60, 100, 150, 50, 104)], [bar(60, 80, 130, 30, 85)]])
    expect(only.high).toBe(189)
    expect(only.low).toBe(180)
  })

  it('carries no volume: a sum of two contracts trades nothing', () => {
    const [only] = foldBars([[bar(60, 1, 1, 1, 1)], [bar(60, 2, 2, 2, 2)]])
    expect(only.volume).toBeUndefined()
  })

  it('folds nothing from one leg, or from three', () => {
    expect(foldBars([[bar(60, 1, 1, 1, 1)]])).toEqual([])
    const one = [bar(60, 1, 1, 1, 1)]
    expect(foldBars([one, one, one])).toEqual([])
  })
})

describe('specProblem', () => {
  it('takes two different instruments and no more', () => {
    expect(specProblem(spec)).toBeNull()
    expect(specProblem({ legs: [CE], op: 'sum' })).toMatch(/needs two/)
    expect(specProblem({ legs: [CE, PE, CE], op: 'sum' })).toMatch(/at most 2/)
    expect(specProblem({ legs: [CE, CE], op: 'sum' })).toMatch(/same instrument/)
    expect(specProblem({ legs: [CE, { symbol: '', exchange: 'NFO' }], op: 'sum' })).toMatch(
      /no contract/
    )
  })
})

describe('LegPrices', () => {
  it('gives no tick until both legs have a price', () => {
    const prices = new LegPrices(2)
    expect(prices.set(0, 142.35, 1000)).toBeNull()
    expect(prices.set(1, 88.1, 1001)).toEqual({ price: 142.35 + 88.1, time: 1001 })
  })

  it('stamps the combined tick with the later exchange time, whichever leg moved', () => {
    const prices = new LegPrices(2)
    prices.set(0, 100, 2000)
    prices.set(1, 50, 1990)
    expect(prices.set(1, 51, 1995)).toEqual({ price: 151, time: 2000 })
    expect(prices.set(0, 101, null)).toEqual({ price: 152, time: 2000 })
  })

  it("starts each leg at its history's last close, which a tick then replaces", () => {
    const prices = new LegPrices(2)
    prices.seed(0, 140)
    prices.seed(1, 90)
    expect(prices.set(0, 142, 500)).toEqual({ price: 232, time: 500 })
    prices.seed(1, 1) // a later seed never speaks over a price
    expect(prices.combined()?.price).toBe(232)
  })

  it('takes 0, a negative or a broken price for no price', () => {
    const prices = new LegPrices(2)
    prices.seed(0, 100)
    prices.seed(1, 50)
    expect(prices.set(0, 0, 10)).toBeNull()
    expect(prices.set(0, Number.NaN, 10)).toBeNull()
    expect(prices.set(1, -4, 10)).toBeNull()
    expect(prices.combined()?.price).toBe(150)
  })
})

describe('ComputedFeed', () => {
  const inner = (answers: Record<string, Bar[]>): DataFeed & { asked: string[] } => {
    const asked: string[] = []
    return {
      asked,
      getBars: async (req) => {
        asked.push(`${req.exchange}:${req.symbol}:${req.interval}`)
        return answers[req.symbol] ?? []
      },
    }
  }

  it('reads each leg as an instrument, once, and folds them', async () => {
    const source = inner({
      [CE.symbol]: [bar(60, 100, 101, 99, 100), bar(120, 100, 103, 100, 102)],
      [PE.symbol]: [bar(60, 80, 81, 79, 80), bar(120, 80, 80, 77, 78)],
    })
    const feed = new ComputedFeed(source)
    feed.setSpec(spec)
    const bars = await feed.getBars({ symbol: computedKey(spec), exchange: '', interval: '5m' })
    expect(source.asked).toEqual([`NFO:${CE.symbol}:5m`, `NFO:${PE.symbol}:5m`])
    expect(bars.map((each) => each.close)).toEqual([180, 180])
    expect(feed.lastCloses).toEqual([102, 78])
  })

  it('fails the read when a side has no candles: a straddle missing a leg is no chart', async () => {
    const feed = new ComputedFeed(inner({ [CE.symbol]: [bar(60, 1, 1, 1, 1)] }))
    feed.setSpec(spec)
    await expect(
      feed.getBars({ symbol: computedKey(spec), exchange: '', interval: '5m' })
    ).rejects.toThrow(/No candles were returned for NIFTY17NOV2625900PE/)
  })

  it('answers nothing for a pair it was not given', async () => {
    const source = inner({})
    const feed = new ComputedFeed(source)
    feed.setSpec(spec)
    expect(await feed.getBars({ symbol: 'NFO:X+NFO:Y', exchange: '', interval: '5m' })).toEqual([])
    expect(source.asked).toEqual([])
  })

  it('never parses a symbol: a hyphen or an ampersand in a name is not arithmetic', async () => {
    const odd: ComputedSpec = {
      legs: [
        { symbol: 'BAJAJ-AUTO24NOV269000CE', exchange: 'NFO' },
        { symbol: 'M&M24NOV263000PE', exchange: 'NFO' },
      ],
      op: 'sum',
    }
    const feed = new ComputedFeed(
      inner({
        'BAJAJ-AUTO24NOV269000CE': [bar(60, 10, 10, 10, 10)],
        'M&M24NOV263000PE': [bar(60, 5, 5, 5, 5)],
      })
    )
    feed.setSpec(odd)
    const bars = await feed.getBars({ symbol: computedKey(odd), exchange: '', interval: '1m' })
    expect(bars).toEqual([{ time: 60, open: 15, high: 15, low: 15, close: 15 }])
  })
})
