import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { AUCTION_CLOSE, AUCTION_PRICE, auctionQuotes, auctionWaiting, inAuction } from './auction'

// 21 Oct 2026, IST: 15:15, 15:30 and 15:35 (UTC+05:30).
const FROM = Date.UTC(2026, 9, 21, 9, 45)
const CLOSE = Date.UTC(2026, 9, 21, 10, 0)
const UNTIL = Date.UTC(2026, 9, 21, 10, 5)

describe('closing auction words', () => {
  it('say when the price comes (the random close, X6j), never Real-time or Stale', () => {
    expect(auctionWaiting(CLOSE)).toBe('Closing auction · price at about 15:30')
    expect(auctionWaiting(CLOSE)).not.toMatch(/Real-time|Stale/)
  })

  it('say why a bid or ask alert is not checked', () => {
    expect(auctionQuotes(FROM)).toBe('Closing auction · bid and ask are not checked after 15:15')
  })

  it('name the print and the close as the server words do', () => {
    expect(AUCTION_PRICE).toBe('closing auction price')
    expect(AUCTION_CLOSE).toBe('auction close')
  })

  it('take the window only from the server', () => {
    expect(inAuction(FROM, FROM, UNTIL)).toBe(true)
    expect(inAuction(UNTIL, FROM, UNTIL)).toBe(true)
    expect(inAuction(FROM - 1, FROM, UNTIL)).toBe(false)
    expect(inAuction(UNTIL + 1, FROM, UNTIL)).toBe(false)
    expect(inAuction(FROM, null, UNTIL)).toBe(false)
    expect(inAuction(FROM, undefined, undefined)).toBe(false)
  })
})

/**
 * Each day of sudarshan/tests/regimes_vectors.json, as the server sends the window of a stock with
 * derivatives that day (sessions.py: 15:15-15:35 with the price at about 15:30 until 6 Dec 2026,
 * 15:30-15:40 with the price at about 15:40 from 7 Dec): the words name it. A stock without
 * derivatives gets no window, so no auction line.
 */
const VECTORS = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'sudarshan', 'tests', 'regimes_vectors.json'), 'utf8')
) as { days: { date: string; auction: [string, string]; auction_close: string }[] }
const at = (date: string, hhmm: string) => {
  const [y, m, d] = date.split('-').map(Number)
  const [hh, mm] = hhmm.split(':').map(Number)
  return Date.UTC(y, m - 1, d, hh, mm) - 19_800_000
}

describe('closing auction words on either side of 7 Dec 2026', () => {
  it.each(VECTORS.days)('$date: the window and the price time the server sends', (day) => {
    const from = at(day.date, day.auction[0])
    const until = at(day.date, day.auction[1])
    expect(auctionWaiting(at(day.date, day.auction_close))).toBe(
      `Closing auction · price at about ${day.auction_close}`
    )
    expect(auctionQuotes(from)).toBe(
      `Closing auction · bid and ask are not checked after ${day.auction[0]}`
    )
    expect(inAuction(at(day.date, '15:34'), from, until)).toBe(true)
    expect(inAuction(at(day.date, '15:36'), from, until)).toBe(day.auction[1] === '15:40')
  })
})
