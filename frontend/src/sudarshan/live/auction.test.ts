import { describe, expect, it } from 'vitest'
import { AUCTION_CLOSE, AUCTION_PRICE, auctionQuotes, auctionWaiting, inAuction } from './auction'

// 21 Oct 2026, IST: 15:15 and 15:35 (UTC+05:30).
const FROM = Date.UTC(2026, 9, 21, 9, 45)
const UNTIL = Date.UTC(2026, 9, 21, 10, 5)

describe('closing auction words', () => {
  it('say when the price comes, never Real-time or Stale', () => {
    expect(auctionWaiting(UNTIL)).toBe('Closing auction · price at about 15:35')
    expect(auctionWaiting(UNTIL)).not.toMatch(/Real-time|Stale/)
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
