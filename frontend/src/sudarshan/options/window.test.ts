import { describe, expect, it } from 'vitest'
import {
  around,
  atmStrike,
  FOLLOW_CAP,
  followed,
  followPlan,
  LIVE_SIDE,
  nextCentre,
  RECENTRE_MIN_MS,
  type StrikeKeys,
} from './window'

const ladder = (count: number, from = 20_000, step = 50): StrikeKeys[] =>
  Array.from({ length: count }, (_, i) => ({
    strike: from + i * step,
    ce: `NFO:T${from + i * step}CE`,
    pe: `NFO:T${from + i * step}PE`,
  }))

describe('the at-the-money strike', () => {
  it('is the strike nearest the price, a tie going to the lower', () => {
    expect(atmStrike(25_912.35, [25_800, 25_850, 25_900, 25_950])).toBe(25_900)
    expect(atmStrike(25_925, [25_950, 25_900])).toBe(25_900)
    expect(atmStrike(null, [1, 2])).toBeNull()
    expect(atmStrike(10, [])).toBeNull()
    expect(atmStrike(Number.NaN, [1])).toBeNull()
  })
})

describe('the window', () => {
  const strikes = ladder(101).map((each) => each.strike)

  it('is the centre and so many strikes either side, cut at the ladder’s ends', () => {
    expect(around(strikes, 22_500, 2)).toEqual([22_400, 22_450, 22_500, 22_550, 22_600])
    expect(around(strikes, 20_000, 2)).toEqual([20_000, 20_050, 20_100])
    expect(around(strikes, null, 1)).toEqual([22_450, 22_500, 22_550])
    expect(around([], 1, 2)).toEqual([])
  })

  it('stays put for a walk of one strike and moves at two', () => {
    const base = { strikes, centre: 22_500, now: 100_000, movedAt: null }
    expect(nextCentre({ ...base, atm: 22_550 })).toBe(22_500)
    expect(nextCentre({ ...base, atm: 22_600 })).toBe(22_600)
    expect(nextCentre({ ...base, atm: 22_350 })).toBe(22_350)
  })

  it('moves at most once in thirty seconds', () => {
    const base = { strikes, centre: 22_500, atm: 22_650, movedAt: 100_000 }
    expect(nextCentre({ ...base, now: 100_000 + RECENTRE_MIN_MS - 1 })).toBe(22_500)
    expect(nextCentre({ ...base, now: 100_000 + RECENTRE_MIN_MS })).toBe(22_650)
  })

  it('takes the at-the-money strike at once with no centre, or one the ladder lost', () => {
    expect(nextCentre({ strikes, centre: null, atm: 22_500, now: 1, movedAt: 1 })).toBe(22_500)
    expect(nextCentre({ strikes, centre: 5, atm: 22_500, now: 1, movedAt: 1 })).toBe(22_500)
    expect(nextCentre({ strikes, centre: 22_500, atm: null, now: 1, movedAt: null })).toBe(22_500)
    expect(nextCentre({ strikes, centre: 5, atm: null, now: 1, movedAt: null })).toBeNull()
  })
})

describe('what the screen follows', () => {
  it('is 84 instruments for an index: 41 strikes, the spot in LTP, the future', () => {
    const plan = followPlan({
      ladder: ladder(101),
      centre: 22_500,
      spot: 'NSE_INDEX:NIFTY',
      spotMode: 'LTP',
      future: 'NFO:NIFTY24NOV26FUT',
    })
    expect(plan.ltp).toEqual(['NSE_INDEX:NIFTY'])
    expect(plan.quote).toHaveLength(83)
    expect(plan.strikes.size).toBe(2 * LIVE_SIDE + 1)
    expect(followed(plan)).toBe(84)
    expect(Math.min(...plan.strikes)).toBe(21_500)
    expect(Math.max(...plan.strikes)).toBe(23_500)
  })

  it('follows a stock’s spot in Quote mode', () => {
    const plan = followPlan({
      ladder: ladder(5),
      centre: 20_100,
      spot: 'NSE:ACME',
      spotMode: 'Quote',
    })
    expect(plan.ltp).toEqual([])
    expect(plan.quote).toContain('NSE:ACME')
    expect(followed(plan)).toBe(11)
  })

  it('never follows a 101st instrument, and never half a strike', () => {
    const sheet = Array.from({ length: 30 }, (_, i) => `NFO:SHEET${i}`)
    const plan = followPlan({
      ladder: ladder(101),
      centre: 22_500,
      spot: 'NSE_INDEX:NIFTY',
      spotMode: 'LTP',
      future: 'NFO:FUT',
      sheet,
      side: 60,
    })
    expect(followed(plan)).toBeLessThanOrEqual(FOLLOW_CAP)
    // 1 + 1 + 30 = 32 held; 68 left is 34 whole strikes, the nearest the centre.
    expect(followed(plan)).toBe(100)
    expect(plan.strikes.size).toBe(34)
    for (const strike of plan.strikes) {
      expect(plan.quote).toContain(`NFO:T${strike}CE`)
      expect(plan.quote).toContain(`NFO:T${strike}PE`)
    }
    expect(plan.strikes.has(22_500)).toBe(true)
    expect(Math.max(...plan.strikes) - Math.min(...plan.strikes)).toBe(33 * 50)
    const odd = followPlan({
      ladder: ladder(101),
      centre: 22_500,
      spot: 'NSE_INDEX:NIFTY',
      spotMode: 'LTP',
      sheet: sheet.slice(0, 16),
      side: 60,
    })
    // 17 held; 83 left is 41 whole strikes and one place nobody takes.
    expect(followed(odd)).toBe(99)
  })

  it('counts a sheet instrument that is also a leg once, and a one-sided strike as one', () => {
    const rungs: StrikeKeys[] = [
      { strike: 100, ce: 'NFO:A' },
      { strike: 110, ce: 'NFO:B', pe: 'NFO:C' },
    ]
    const plan = followPlan({
      ladder: rungs,
      centre: 110,
      spot: null,
      spotMode: 'LTP',
      sheet: ['NFO:B'],
    })
    expect([...plan.quote].sort()).toEqual(['NFO:A', 'NFO:B', 'NFO:C'])
    expect(plan.strikes).toEqual(new Set([100, 110]))
  })

  it('changes by difference when the centre walks: the strikes that stay are in both plans', () => {
    const args = { ladder: ladder(101), spot: 'NSE_INDEX:NIFTY', spotMode: 'LTP' as const }
    const before = new Set(followPlan({ ...args, centre: 22_500 }).quote)
    const after = new Set(followPlan({ ...args, centre: 22_650 }).quote)
    const left = [...before].filter((key) => !after.has(key))
    const joined = [...after].filter((key) => !before.has(key))
    expect(left).toHaveLength(6)
    expect(joined).toHaveLength(6)
  })
})
