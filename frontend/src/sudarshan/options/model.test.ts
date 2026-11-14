import { describe, expect, it } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'
import {
  BUILD_UP_WORDS,
  buildUpOf,
  type ChainModel,
  type ChainSnapshot,
  chainModel,
  maxPain,
} from './model'
import vectors from './vectors.json'

interface Case {
  name: string
  now: number
  snapshot: ChainSnapshot
  ticks: Record<string, { at: number; data: SymbolData['data'] }>
  followed?: number[]
  spot: number | null
  future: number | null
  expect: Record<string, unknown>
}

const CASES = vectors.cases as unknown as Case[]

function run(c: Case): ChainModel {
  const lines = new Map<string, SymbolData>()
  for (const [key, tick] of Object.entries(c.ticks)) {
    const [exchange, symbol] = key.split(':')
    lines.set(key, { symbol, exchange, data: tick.data, lastUpdate: tick.at })
  }
  return chainModel({
    snapshot: c.snapshot,
    lines,
    followed: c.followed ? new Set(c.followed) : null,
    spot: c.spot,
    future: c.future,
    now: c.now,
  })
}

/** Every key the vector names must match; a number to nine places. */
function holds(got: unknown, want: unknown, path: string) {
  if (typeof want === 'number') {
    expect(got, path).toBeTypeOf('number')
    expect(got as number, path).toBeCloseTo(want, 9)
  } else if (want === null || typeof want !== 'object') {
    expect(got, path).toBe(want)
  } else {
    expect(got, path).not.toBeNull()
    for (const [key, value] of Object.entries(want))
      holds((got as Record<string, unknown>)[key], value, `${path}.${key}`)
  }
}

describe('the chain model against its vectors', () => {
  it.each(CASES.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const model = run(c)
    const { rows, greeks, ...figures } = c.expect as {
      rows?: Record<string, unknown>
      greeks?: boolean
    } & Record<string, unknown>
    holds(model, figures, 'model')
    for (const [strike, want] of Object.entries(rows ?? {})) {
      const row = model.rows.find((each) => each.strike === Number(strike))
      holds(row, want, `row ${strike}`)
    }
    if (greeks !== undefined) {
      const worked = model.rows.flatMap((row) => [row.ce?.greeks, row.pe?.greeks])
      if (greeks) for (const each of worked) expect(Number.isFinite(each?.delta)).toBe(true)
      else for (const each of worked) expect(each).toBeNull()
    }
  })

  it('agrees with the wide page on max pain (cases from its own function)', () => {
    expect(vectors.maxPain.length).toBeGreaterThanOrEqual(3)
    for (const golden of vectors.maxPain) {
      const rows = golden.chain.map((row) => ({
        strike: row.strike,
        ceOi: row.ce_oi,
        peOi: row.pe_oi,
      }))
      expect(maxPain(rows, null)).toBe(golden.max_pain)
      expect(maxPain(rows, rows[0].strike)).toBe(golden.max_pain)
    }
  })
})

describe('the chain model', () => {
  const plain = CASES[0]

  it('never reads a missing previous open interest as 0', () => {
    const snapshot = structuredClone(plain.snapshot)
    delete snapshot.strikes[0].ce?.prev_oi
    const ce = run({ ...plain, snapshot }).rows[0].ce
    expect(ce?.oi).toBe(100)
    expect(ce?.oiChange).toBeNull()
    expect(ce?.oiChangePct).toBeNull()
    expect(ce?.buildUp).toBeNull()
  })

  it('works out IV and the Greeks for the followed strikes only, from the forward', () => {
    const model = run({ ...plain, followed: [110, 120, 130] })
    const by = new Map(model.rows.map((row) => [row.strike, row]))
    expect(by.get(100)?.ce?.greeks).toBeNull()
    expect(by.get(140)?.pe?.greeks).toBeNull()
    const atm = by.get(120)
    expect(atm?.ce?.greeks?.iv).toBeGreaterThan(0)
    expect(atm?.ce?.greeks?.delta).toBeGreaterThan(0)
    expect(atm?.pe?.greeks?.delta).toBeLessThan(0)
    expect(model.atmIv).toBeGreaterThan(0)
    // 110 and 130 are one strike from the money: two away is outside what is followed here.
    expect(model.skew).toBeNull()
    const whole = run(plain)
    expect(whole.skew).toBeCloseTo(
      (whole.rows[0].pe?.greeks?.iv ?? 0) - (whole.rows[4].ce?.greeks?.iv ?? 0),
      9
    )
  })

  it('keeps the broker IV beside its own and shows a price of 0 as no price', () => {
    const snapshot = structuredClone(plain.snapshot)
    const ce = snapshot.strikes[2].ce
    if (ce) Object.assign(ce, { iv: 11.8, ltp: 0, bid: 0, ask: 0 })
    const model = run({ ...plain, snapshot })
    const leg = model.rows[2].ce
    expect(leg?.brokerIv).toBe(11.8)
    expect(leg?.ltp).toBeNull()
    expect(leg?.greeks).toBeNull()
    expect(model.expectedMove).toBeNull()
    expect(model.forwardFrom).toBe('spot')
  })

  it('has no spot, forward, walls or at-the-money strike when nobody sent a price', () => {
    const model = run({ ...plain, snapshot: { ...plain.snapshot, spot: null } })
    expect(model.spot).toBeNull()
    expect(model.atm).toBeNull()
    expect(model.forward).toBeNull()
    expect(model.callWall).toBeNull()
    expect(model.maxPain).toBe(120)
    expect(model.rows.every((row) => !row.atm)).toBe(true)
  })

  it('names the four build-ups and nothing for a figure that did not move', () => {
    expect(buildUpOf(1, 1)).toBe('long_buildup')
    expect(buildUpOf(-1, 1)).toBe('short_buildup')
    expect(buildUpOf(1, -1)).toBe('short_covering')
    expect(buildUpOf(-1, -1)).toBe('long_unwinding')
    for (const pair of [
      [0, 1],
      [1, 0],
      [null, 1],
      [1, null],
    ] as const)
      expect(buildUpOf(pair[0], pair[1])).toBeNull()
    expect(BUILD_UP_WORDS.short_covering).toBe('Short covering')
  })

  it('says when no followed leg has had its open interest from a tick', () => {
    expect(run(plain).oiFromSnapshot).toBe(true)
    const ticking = run({
      ...plain,
      ticks: { 'NFO:TEST120CE': { at: plain.now, data: { oi: 310 } } },
    })
    expect(ticking.oiFromSnapshot).toBe(false)
    const priceOnly = run({
      ...plain,
      ticks: { 'NFO:TEST120CE': { at: plain.now, data: { ltp: 6.5 } } },
    })
    expect(priceOnly.oiFromSnapshot).toBe(true)
  })

  it('has no max pain without any open interest', () => {
    expect(maxPain([{ strike: 100, ceOi: null, peOi: 0 }], 100)).toBeNull()
  })
})
