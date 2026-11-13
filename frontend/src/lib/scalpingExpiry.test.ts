import { describe, expect, it } from 'vitest'
import { pickExpiry, rollNotice, rollRefetchMs } from './scalpingExpiry'

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

describe('the notice when the expiry moves on (review N5)', () => {
  it('names the expiry that stopped, its stop when the page knew it, and the one now shown', () => {
    const stop = { expiry: '08DEC26', at: dec8(15, 30) }
    expect(rollNotice('08DEC26', '15DEC26', stop)).toBe(
      'The 08DEC26 expiry stopped trading at 15:30. Now showing 15DEC26.'
    )
    // A page left open overnight, or one that never heard the stop: no time is guessed.
    expect(rollNotice('08DEC26', '15DEC26', null)).toBe(
      'The 08DEC26 expiry is no longer traded. Now showing 15DEC26.'
    )
    expect(rollNotice('08DEC26', '15DEC26', { expiry: '01DEC26', at: dec8(15, 30) })).toBe(
      'The 08DEC26 expiry is no longer traded. Now showing 15DEC26.'
    )
  })

  it('says nothing on the first load or when nothing moved', () => {
    expect(rollNotice('', '08DEC26')).toBeNull()
    expect(rollNotice('15DEC26', '15DEC26')).toBeNull()
  })
})
