import { describe } from '../rows'
import type { RankAsset, RankCategory, RankSource } from './types'

/**
 * M8.9 F16: what a ranked list says, for a trader, and its timing rules. Pure: the caller passes
 * the facts (and the clock) in. The words are the server's (`words/ranked.py`); the age line is
 * redrawn on the phone from `made_at`, so the same sentences are written here and a test holds
 * them. A ranking says "Ranked by Upstox" and its age, never "Live".
 */
export const RETURN_AFTER_MS = 15_000
/** The Refresh ranking button is off, and a pull is answered with the cached read, for this long. */
export const HOLD_MS = 60_000
export const STALE_MS = 10 * 60_000
/** "Ranking updated ..." stays this long after a read that changed the order. */
export const UPDATED_MS = 60_000

export const SOURCE_WORDS: Record<RankSource, string> = { futures: 'Futures', options: 'Options' }
export const ASSET_WORDS: Record<RankAsset, string> = {
  INDEX: 'Index',
  STOCK: 'Stock',
  COMMODITY: 'Commodity',
}
export const CATEGORY_WORDS: Record<RankCategory, string> = {
  TOP_TRADED: 'Top traded',
  MOST_ACTIVE: 'Most active',
  OI_GAINERS: 'OI gainers',
  OI_LOSERS: 'OI losers',
  PRICE_GAINERS: 'Price gainers',
  PRICE_LOSERS: 'Price losers',
  PREMIUM: 'Premium',
  DISCOUNT: 'Discount',
  IV_GAINERS: 'IV gainers',
  IV_LOSERS: 'IV losers',
  UNDER_5000: 'Under ₹5,000 to buy',
  UNDER_10000: 'Under ₹10,000 to buy',
}

export interface RankedParts {
  src: RankSource
  asset: RankAsset
  cat: RankCategory
}

/** `ranked:futures:INDEX:OI_GAINERS` as its three parts; null for anything that is not offered. */
export function parseRankedRef(ref: string | null): RankedParts | null {
  const parts = ref?.split(':') ?? []
  if (parts.length !== 4 || parts[0] !== 'ranked') return null
  const [, src, asset, cat] = parts
  if (!(src in SOURCE_WORDS) || !(asset in ASSET_WORDS) || !(cat in CATEGORY_WORDS)) return null
  return { src: src as RankSource, asset: asset as RankAsset, cat: cat as RankCategory }
}

/** "Index futures · OI gainers", as the server names it. */
export function rankedLabel(ref: string): string {
  const got = parseRankedRef(ref)
  if (!got) return 'Ranked list'
  return `${ASSET_WORDS[got.asset]} ${got.src} · ${CATEGORY_WORDS[got.cat]}`
}

const HMS = new Intl.DateTimeFormat('en-IN', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Kolkata',
})
const HM = new Intl.DateTimeFormat('en-IN', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Kolkata',
})

/** "just now", "6 min ago", "2 h ago", "2 d ago". */
export function ageWords(madeMs: number, nowMs: number): string {
  const s = Math.max(0, Math.floor((nowMs - madeMs) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86_400)} d ago`
}

export const isStale = (madeMs: number, nowMs: number) => nowMs - madeMs > STALE_MS

/** The age line: "Ranked by Upstox · 2 min ago", with the way out once it is stale. */
export function ageLine(madeMs: number, nowMs: number): { text: string; stale: boolean } {
  const stale = isStale(madeMs, nowMs)
  const text = `Ranked by Upstox · ${ageWords(madeMs, nowMs)}`
  return { text: stale ? `${text} · Pull down to read it again` : text, stale }
}

/** A tap on the age line: "Ranking made 10:42:05, read 10:43:10". */
export const timesLine = (madeMs: number, readMs: number) =>
  `Ranking made ${HMS.format(madeMs)}, read ${HMS.format(readMs)}`

/** The Refresh ranking button inside its minute. */
export const readAgo = (gotMs: number, nowMs: number) =>
  `Read ${Math.max(0, Math.floor((nowMs - gotMs) / 1000))} s ago`

/** The server's left-out sentence, which it sends at the end of `words`; else one of the page's own. */
export function leftOutSentence(words: string, n: number): string | null {
  if (n <= 0) return null
  const found = /\d+ contracts? from Upstox .*$/.exec(words)
  if (found) return found[0]
  const one = n === 1
  return `${n} contract${one ? '' : 's'} from Upstox ${one ? 'is' : 'are'} not in today's contract list and ${one ? 'is' : 'are'} left out.`
}

/** "Ranking updated 10:43 · 3 new: NIFTY 25,950 CE, ...", or the order alone when nothing is new. */
export function updatedLine(readMs: number, names: readonly string[]): string {
  const at = `Ranking updated ${HM.format(readMs)}`
  if (names.length === 0) return `${at} · the order changed`
  const shown = names.slice(0, 3).join(', ')
  const more = names.length > 3 ? ` and ${names.length - 3} more` : ''
  return `${at} · ${names.length} new: ${shown}${more}`
}

/** "12 of the top 50 are NIFTY contracts". */
export const underlyingChip = (count: number, total: number, underlying: string) =>
  `${count} of the top ${total} are ${underlying} contracts`

/** The underlying of a ranked contract ("NIFTY" for NIFTY25NOV2625950CE). */
export const underlyingOf = (symbol: string, exchange: string) =>
  describe(symbol, exchange).underlying

/** A contract's short name for a line of words: "NIFTY 25,950 CE". */
export function shortName(symbol: string, exchange: string): string {
  const title = describe(symbol, exchange).title
  return title.replace(/ (\d{4,})( [CP]E)$/, (_, strike: string, right: string) => {
    return ` ${Number(strike).toLocaleString('en-IN')}${right}`
  })
}
