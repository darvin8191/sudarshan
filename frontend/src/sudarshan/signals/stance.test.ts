/**
 * M13.9 (U6): the owner's open index F&O against the regime, direction only (design 3.7.5 and
 * section 14), held to stance.vectors.json: which rows are legs on NIFTY or BANKNIFTY and which way
 * each leans, what the legs add up to, and that beside the regime's bias.
 */
import { describe, expect, it } from 'vitest'
import {
  type Direction,
  directionOf,
  directionWords,
  legOf,
  legWords,
  STANCE_WORDS,
  stanceOf,
  underlyingOf,
} from './stance'
import vectors from './stance.vectors.json'
import type { Bias, Instrument } from './types'

describe('the underlying index of a chart or a row', () => {
  for (const vector of vectors.underlying) {
    it(`${vector.symbol} on ${vector.exchange} is ${vector.instrument ?? 'neither index'}`, () => {
      expect(underlyingOf(vector.symbol, vector.exchange)).toBe(vector.instrument)
    })
  }

  it('anything that is not text is neither', () => {
    expect(underlyingOf(undefined, 'NFO')).toBeNull()
    expect(underlyingOf('NIFTY', null)).toBeNull()
  })
})

describe('one row as a leg', () => {
  for (const vector of vectors.legs) {
    it(vector.about, () => {
      expect(legOf(vector.row)).toEqual(vector.leg)
    })
  }
})

describe('what the legs on one index add up to', () => {
  for (const vector of vectors.directions) {
    it(vector.about, () => {
      expect(directionOf(vector.rows, vector.instrument as Instrument).direction).toBe(
        vector.direction
      )
    })
  }
})

describe('beside the regime', () => {
  for (const vector of vectors.stances) {
    it(`${vector.direction} with a ${vector.bias ?? 'missing'} regime is ${vector.stance}`, () => {
      expect(stanceOf(vector.direction as Direction, vector.bias as Bias | null)).toBe(
        vector.stance
      )
    })
  }

  it('says each stance in words, "with" and "against" as the design names them', () => {
    expect(STANCE_WORDS.with).toBe('With the regime')
    expect(STANCE_WORDS.against).toBe('Against the regime')
    for (const words of Object.values(STANCE_WORDS)) expect(words).not.toMatch(/arm|live/i)
  })

  it('says where he stands without sizes, and what his legs are', () => {
    const rows =
      vectors.directions.find((each) => each.about.startsWith('calls bought'))?.rows ?? []
    const { direction, legs } = directionOf(rows, 'NIFTY')
    expect(directionWords(direction, 'NIFTY')).toBe('You are long NIFTY.')
    expect(legWords(legs)).toBe('calls bought, puts sold')
    expect(directionWords('none', 'BANKNIFTY')).toBe('No open BANKNIFTY futures or options.')
    expect(directionWords('mixed', 'NIFTY')).toBe('You hold long and short NIFTY positions.')
    expect(legWords([{ instrument: 'NIFTY', kind: 'future', side: 'short' }])).toBe('futures sold')
  })
})
