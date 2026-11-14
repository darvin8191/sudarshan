/** The straddle's figures, and the feed budget it has to stay inside (M11.4, O8). */
import { describe, expect, it } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'
import { chainOf, NOW } from './fixtures'
import { type ChainModel, chainModel } from './model'
import {
  legTick,
  stepStrike,
  straddleFigures,
  straddleFollows,
  straddleStart,
  straddleStrikes,
} from './straddle'
import { around, FOLLOW_CAP, followed, followPlan, type StrikeKeys } from './window'

const key = (strike: number, side: 'CE' | 'PE') => `NFO:NIFTY17NOV26${strike}${side}`

function modelOf(edit?: (chain: ReturnType<typeof chainOf>) => void): ChainModel {
  const chain = chainOf()
  edit?.(chain)
  const strikes = chain.strikes.map((each) => each.strike)
  return chainModel({
    snapshot: chain,
    lines: new Map(),
    followed: new Set(around(strikes, 25_900, 20)),
    now: NOW + 1000,
  })
}
const rowOf = (model: ChainModel, strike: number) =>
  model.rows.find((each) => each.strike === strike)

describe('the straddle’s figures', () => {
  it('adds the two premiums, and reads the change against the two closes', () => {
    const model = modelOf((chain) => {
      const at = chain.strikes.find((each) => each.strike === 25_900)
      Object.assign(at?.ce ?? {}, { ltp: 142.35, close: 130.25 })
      Object.assign(at?.pe ?? {}, { ltp: 88.1, close: 98.1 })
    })
    const figures = straddleFigures(rowOf(model, 25_900), 25_912.35)
    expect(figures?.premium).toBeCloseTo(230.45, 2)
    expect(figures?.change).toBeCloseTo(2.1, 2)
    expect(figures?.changePct).toBeCloseTo((2.1 / 228.35) * 100, 4)
    expect(figures?.lower).toBeCloseTo(25_669.55, 2)
    expect(figures?.upper).toBeCloseTo(26_130.45, 2)
    expect(figures?.ofSpotPct).toBeCloseTo((230.45 / 25_912.35) * 100, 4)
    expect(figures).toMatchObject({ atm: true, live: true, strike: 25_900 })
  })

  it('says nothing it does not know: a side with no price gives no premium, never a half one', () => {
    const model = modelOf((chain) => {
      const at = chain.strikes.find((each) => each.strike === 25_900)
      if (at?.pe) at.pe.ltp = 0
    })
    expect(straddleFigures(rowOf(model, 25_900), 25_912.35)).toMatchObject({
      premium: null,
      change: null,
      changePct: null,
      lower: null,
      upper: null,
      ofSpotPct: null,
    })
    expect(straddleFigures(undefined, 1)).toBeNull()
  })

  it('gives a change only when both closes are known, and no percent of an unknown spot', () => {
    const model = modelOf((chain) => {
      const at = chain.strikes.find((each) => each.strike === 25_900)
      if (at?.ce) delete at.ce.close
    })
    const figures = straddleFigures(rowOf(model, 25_900), null)
    expect(figures?.premium).not.toBeNull()
    expect(figures).toMatchObject({ change: null, changePct: null, ofSpotPct: null })
  })
})

describe('the strike a straddle stands on', () => {
  it('opens on the strike asked for, else on the money', () => {
    const model = modelOf()
    expect(straddleStart(model, 26_100)).toBe(26_100)
    expect(straddleStart(model, null)).toBe(25_900)
    expect(straddleStart(model, 12_345)).toBe(25_900)
  })

  it('skips a strike that lists one side only', () => {
    const model = modelOf((chain) => {
      const at = chain.strikes.find((each) => each.strike === 25_900)
      if (at) delete at.pe
    })
    expect(straddleStrikes(model)).not.toContain(25_900)
    expect(straddleStart(model, 25_900)).toBe(25_850)
    expect(stepStrike(model, 25_850, 1)).toBe(25_950)
  })

  it('steps one strike at a time and stops at the ends', () => {
    const model = modelOf()
    expect(stepStrike(model, 25_900, 1)).toBe(25_950)
    expect(stepStrike(model, 25_900, -1)).toBe(25_850)
    expect(stepStrike(model, 24_500, -1)).toBe(24_500)
    expect(stepStrike(model, 27_500, 1)).toBe(27_500)
  })
})

describe('the feed budget (lane O: 84 for an index chain, never more than 100)', () => {
  const chain = chainOf()
  const ladder: StrikeKeys[] = chain.strikes.map((each) => ({
    strike: each.strike,
    ce: key(each.strike, 'CE'),
    pe: key(each.strike, 'PE'),
  }))
  const plan = (sheet: string[]) =>
    followPlan({
      ladder,
      centre: 25_900,
      spot: 'NSE_INDEX:NIFTY',
      spotMode: 'LTP',
      future: 'NFO:NIFTY24NOV26FUT',
      sheet,
    })
  const model = modelOf()

  it('a straddle inside the window follows nothing new: still 84 instruments', () => {
    const base = plan([])
    expect(followed(base)).toBe(84)
    for (const strike of [25_900, 24_900, 26_900]) {
      const extra = straddleFollows(rowOf(model, strike), base.strikes)
      expect(extra).toEqual([])
      expect(followed(plan(extra))).toBe(84)
    }
  })

  it('a far strike adds exactly its two legs: 86', () => {
    const base = plan([])
    const extra = straddleFollows(rowOf(model, 24_500), base.strikes)
    expect(extra).toEqual([key(24_500, 'CE'), key(24_500, 'PE')])
    const next = plan(extra)
    expect(followed(next)).toBe(86)
    expect(next.quote).toEqual(expect.arrayContaining(extra))
    // The window is whole: no strike was dropped to make room.
    expect(next.strikes.size).toBe(41)
  })

  it('with the strike sheet open over it (2 legs and 8 of later expiries) it is 94, under the cap', () => {
    const base = plan([])
    const far = straddleFollows(rowOf(model, 24_500), base.strikes)
    const sheet = [
      ...far,
      ...Array.from({ length: 8 }, (_, i) => `NFO:LATER${i}`), // the same strike, four later expiries
    ]
    const next = plan([...far, ...sheet])
    expect(followed(next)).toBe(94)
    expect(followed(next)).toBeLessThanOrEqual(FOLLOW_CAP)
  })

  it('whatever strike is shown, on any chain size, the screen never follows a 101st instrument', () => {
    for (const strike of model.rows.map((each) => each.strike)) {
      const base = plan([])
      const far = straddleFollows(rowOf(model, strike), base.strikes)
      const crowded = [...far, ...Array.from({ length: 30 }, (_, i) => `NFO:OTHER${i}`)]
      expect(followed(plan(crowded))).toBeLessThanOrEqual(FOLLOW_CAP)
    }
  })

  it('a strike listing one side only has no straddle to follow', () => {
    const one = modelOf((chain) => {
      const at = chain.strikes.find((each) => each.strike === 24_500)
      if (at) delete at.ce
    })
    expect(straddleFollows(rowOf(one, 24_500), new Set())).toEqual([])
  })
})

describe('a leg’s tick for the chart', () => {
  const line = (data: SymbolData['data'], source: SymbolData['updateSource'] = 'websocket') =>
    ({ symbol: 'X', exchange: 'NFO', data, lastUpdate: NOW, updateSource: source }) as SymbolData

  it('is the price with the exchange’s trade time in seconds', () => {
    expect(legTick(line({ ltp: 142.35, last_trade_time: 1_794_000_000_500 }))).toMatchObject({
      price: 142.35,
      time: 1_794_000_000,
    })
    expect(legTick(line({ ltp: 5, ltt: 1_794_000_010 }))?.time).toBe(1_794_000_010)
    expect(legTick(line({ ltp: 5 }))?.time).toBeNull()
  })

  it('is nothing for a quote read standing in for the stream, or for no price', () => {
    expect(legTick(line({ ltp: 142.35 }, 'rest'))).toBeNull()
    expect(legTick(line({ ltp: 0 }))).toBeNull()
    expect(legTick(line({}))).toBeNull()
    expect(legTick(undefined)).toBeNull()
  })
})
