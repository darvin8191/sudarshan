import { describe, expect, it } from 'vitest'
import { straddleOf } from './strikeSheet'

describe('the strike sheet rules', () => {
  it('adds the straddle only when both sides have a price', () => {
    expect(straddleOf(200.3, 140.25)).toBeCloseTo(340.55)
    expect(straddleOf(200, null)).toBeNull()
  })
})
