import { describe, expect, it } from 'vitest'
import { pickExpiry, rollRefetchMs } from './scalpingExpiry'

/** Epoch ms of a time in India on Tuesday 8 December 2026, a NIFTY expiry. */
const dec8 = (h: number, mi: number) => Date.UTC(2026, 11, 8, h, mi) - 19_800_000

describe("the scalping terminal's expiry on an expiry day (review M3)", () => {
  it('asks for the list again just after the stop the server names, and not otherwise', () => {
    expect(rollRefetchMs(dec8(15, 30), dec8(15, 20))).toBe(10 * 60_000 + 500)
    expect(rollRefetchMs(dec8(15, 30), dec8(15, 31))).toBe(false)
    expect(rollRefetchMs(null, dec8(15, 20))).toBe(false)
    expect(rollRefetchMs(undefined)).toBe(false)
  })

  it('moves to the nearest expiry offered once the one picked has stopped', () => {
    expect(pickExpiry('08DEC26', ['15DEC26', '22DEC26'])).toBe('15DEC26')
    expect(pickExpiry('', ['08DEC26', '15DEC26'])).toBe('08DEC26')
    // A later expiry the trader picked stays picked while it is offered.
    expect(pickExpiry('22DEC26', ['15DEC26', '22DEC26'])).toBe('22DEC26')
    expect(pickExpiry('08DEC26', [])).toBe('08DEC26')
  })
})
