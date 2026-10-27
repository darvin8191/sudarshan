/**
 * Words for a stock's closing auction (cross-design X6e), one source for every screen.
 *
 * Since 3 Aug 2026 (NSE/CMTR/75479) a stock with derivatives stops continuous
 * trading at 15:15 and closes by auction: orders 15:20-15:30, matched
 * 15:30-15:35. Until its auction's price comes, its last price is not
 * "Real-time" and not "Stale": it is waiting for the auction. The price that
 * comes is the closing auction price, and the stock's close for the day is the
 * auction close.
 *
 * The phone never computes sessions. Every row the server sends for such a
 * stock carries `auction_from` and `auction_until` (epoch ms, from
 * sudarshan/alerts/market/sessions.py); these words take them as they come.
 * The Python side says the same in sudarshan/alerts/market/words.py.
 */

const IST_MINUTE = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** "15:35": a moment as the clock in India shows it, to the minute. */
function istMinute(at: number): string {
  return IST_MINUTE.format(at)
}

/** The auction's print, named where a price is shown. */
export const AUCTION_PRICE = 'closing auction price'

/** A stock's close for the day when it closes by auction. */
export const AUCTION_CLOSE = 'auction close'

/** From the end of continuous trading until the print: "Closing auction · price at about 15:35". */
export function auctionWaiting(auctionUntil: number): string {
  return `Closing auction · price at about ${istMinute(auctionUntil)}`
}

/** An alert on the bid or ask after continuous trading ends: the book is the auction's. */
export function auctionQuotes(auctionFrom: number): string {
  return `Closing auction · bid and ask are not checked after ${istMinute(auctionFrom)}`
}

/** Whether `now` falls in the window the server gave; false for a stock without one. */
export function inAuction(
  now: number,
  auctionFrom: number | null | undefined,
  auctionUntil: number | null | undefined
): boolean {
  if (auctionFrom == null || auctionUntil == null) return false
  return auctionFrom <= now && now <= auctionUntil
}
