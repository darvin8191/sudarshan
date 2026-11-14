/**
 * The status row's market and expiry chips (design 3.3, D2) from the server's day facts and a
 * clock: every row of the chip table, read off the day facts the server is pinned to
 * (sudarshan/tests/regimes_vectors.json, `day_facts`), on both sides of 7 Dec 2026.
 */
import { describe, expect, it } from 'vitest'
import { at, serverFacts, VECTOR_DAYS } from '@/test/dayFacts'
import { expiryChip, marketChip, nextEdge, readDayFacts } from './marketPhase'

/** A vector day's facts as the phone reads them. */
function factsOf(day: string) {
  const facts = readDayFacts(serverFacts(day))
  if (!facts) throw new Error(`the ${day} vector did not read`)
  return facts
}

const words = (day: string, hhmm: string) => {
  const facts = factsOf(day)
  return {
    market: marketChip(facts, false, at(day, hhmm)).words,
    expiry: expiryChip(facts, at(day, hhmm))?.words ?? null,
  }
}

describe('the market chip', () => {
  it.each([
    ['2026-11-16', '08:00', 'Opens 09:15'],
    ['2026-11-16', '09:05', 'Pre-open · opens 09:15'],
    ['2026-11-16', '11:00', 'Open · closes 15:30'],
    ['2026-11-16', '15:35', 'Cash closed · F&O to 15:40'],
    ['2026-11-16', '16:00', 'Closed · opens Tue 09:15'],
    ['2026-11-14', '10:00', 'Weekend · opens Mon 09:15'],
    ['2026-11-24', '11:00', 'Holiday · opens Wed 09:15'],
    ['2026-11-08', '12:00', 'Special session 14:20-15:30'],
    ['2026-11-08', '14:10', 'Pre-open · opens 14:20'],
    ['2026-11-08', '15:00', 'Open · closes 15:30'],
    ['2026-11-08', '15:35', 'Cash closed · F&O to 15:40'],
    ['2026-11-08', '16:00', 'Closed · opens Mon 09:15'],
    ['2026-12-08', '15:35', 'Closing auction · price at about 15:40'],
    ['2026-12-08', '15:42', 'Cash closed · F&O to 15:45'],
    ['2026-12-08', '15:50', 'Closed · opens Wed 09:15'],
  ])('%s at %s says "%s"', (day, hhmm, expected) => {
    expect(words(day, hhmm).market).toBe(expected)
  })

  it('pairs each state with its own icon and tone, so it never rests on colour', () => {
    const facts = factsOf('2026-11-16')
    const chip = (hhmm: string) => marketChip(facts, false, at('2026-11-16', hhmm))
    expect([chip('08:00').icon, chip('08:00').tone]).toEqual(['clock', 'neutral'])
    expect([chip('09:05').icon, chip('09:05').tone]).toEqual(['clock', 'gold'])
    expect([chip('11:00').icon, chip('11:00').tone]).toEqual(['dot', 'live'])
    expect([chip('16:00').icon, chip('16:00').tone]).toEqual(['moon', 'neutral'])
  })

  it('says the hours were not read, or are being read, and never calls a holiday "Open"', () => {
    expect(marketChip(null, true, Date.now())).toMatchObject({
      words: 'Market hours not read',
      icon: 'alert',
    })
    expect(marketChip(null, false, Date.now()).words).toBe('Reading market hours')
    const holiday = factsOf('2026-11-24')
    for (const hhmm of ['09:00', '11:00', '15:35', '17:30']) {
      expect(marketChip(holiday, false, at('2026-11-24', hhmm)).kind).toBe('holiday')
    }
  })

  it('names the date when the next open is a week or more away', () => {
    const holiday = { ...factsOf('2026-11-24'), nextOpen: at('2026-12-01', '09:15') }
    expect(marketChip(holiday, false, at('2026-11-24', '11:00')).words).toBe(
      'Holiday · opens Tue 1 Dec 09:15'
    )
  })

  it("never claims prices are arriving: that is each card's line", () => {
    for (const day of VECTOR_DAYS) {
      for (const hhmm of ['06:00', '09:10', '12:00', '15:35', '15:44', '20:00']) {
        expect(words(day, hhmm).market).not.toMatch(/real-time|live/i)
      }
    }
  })
})

describe('the expiry chip', () => {
  it.each([
    ['2026-11-17', '11:00', 'Expiry today: NIFTY · stops 15:40'],
    ['2026-11-17', '15:41', null],
    ['2026-11-16', '11:00', null],
    ['2026-11-16', '15:30', 'Expiry tomorrow: NIFTY'],
    ['2026-11-23', '10:00', 'Expiry today: NIFTY, BANKNIFTY · stops 15:40'],
    ['2026-12-08', '11:00', 'Expiry today: NIFTY · stops 15:30'],
    ['2026-12-08', '15:35', null],
    ['2026-12-29', '11:00', 'Expiry today: NIFTY, BANKNIFTY · stops 15:30'],
    ['2026-11-14', '10:00', null],
  ])('%s at %s says %s', (day, hhmm, expected) => {
    expect(words(day, hhmm).expiry).toBe(expected)
  })

  it('names the weekday when the next trading day is not tomorrow, and shows it all day on a closed day', () => {
    const saturday = {
      ...factsOf('2026-11-14'),
      expiries: [{ underlying: 'NIFTY', exchange: 'NFO', day: '2026-11-16', stopsAt: null }],
    }
    expect(expiryChip(saturday, at('2026-11-14', '07:00'))?.words).toBe('Expiry Mon: NIFTY')
  })

  it('names the first expiry it speaks of, for the chain a tap opens (O9)', () => {
    expect(expiryChip(factsOf('2026-11-17'), at('2026-11-17', '11:00'))?.chain).toEqual({
      exchange: 'NFO',
      underlying: 'NIFTY',
      day: '2026-11-17',
    })
    expect(expiryChip(factsOf('2026-11-16'), at('2026-11-16', '16:00'))?.chain).toMatchObject({
      underlying: 'NIFTY',
      day: '2026-11-17',
    })
  })

  it('says nothing while the master contract is not loaded', () => {
    expect(
      expiryChip({ ...factsOf('2026-11-17'), expiries: null }, at('2026-11-17', '11:00'))
    ).toBe(null)
  })
})

describe('when the row looks again', () => {
  it('waits for the next moment a chip changes, never on a repeating timer', () => {
    const facts = factsOf('2026-11-17')
    expect(nextEdge(facts, at('2026-11-17', '08:00'))).toBe(at('2026-11-17', '09:00'))
    expect(nextEdge(facts, at('2026-11-17', '11:00'))).toBe(at('2026-11-17', '15:30'))
    expect(nextEdge(facts, at('2026-11-17', '15:31'))).toBe(at('2026-11-17', '15:40'))
    expect(nextEdge(null, Date.now())).toBe(null)
  })

  it("reads the server's answer defensively", () => {
    expect(readDayFacts(null)).toBe(null)
    expect(readDayFacts({ day: '2026-11-17' })).toBe(null)
    const facts = readDayFacts({
      day: '2026-11-17',
      trading_day: true,
      exchanges: { NSE: { open: 1, close: 2 }, NFO: { open: null, close: 5 } },
      expiries: 'not a list',
      next_open: 0,
    })
    expect(facts?.exchanges).toEqual({
      NSE: { preOpen: null, open: 1, close: 2, auctionUntil: null },
    })
    expect(facts?.expiries).toBe(null)
    expect(facts?.nextOpen).toBe(null)
  })
})
