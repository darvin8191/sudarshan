/**
 * SUDARSHAN M3.5, M3.6 (D2): the status row's market and expiry chips, as plain functions of the
 * day's facts and the clock. The facts are the server's (GET /sudarshan/session/today,
 * sudarshan/instrument_session.py): the phone never works out a session. The market chip states
 * NSE's cash session, from the calendar: it never says whether prices are arriving, which each
 * card's live line says.
 */

export interface ExchangeHours {
  preOpen: number | null
  open: number
  close: number
  /** From 7 Dec 2026 the cash market's closing auction, which prices at about this moment. */
  auctionUntil: number | null
}
export interface IndexExpiry {
  underlying: string
  exchange: string
  day: string
  stopsAt: number | null
}
export interface StockExpiry {
  exchange: string
  day: string
  count: number
  stopsAt: number | null
}
export interface DayFacts {
  day: string
  tradingDay: boolean
  /** "Weekend", "Holiday", "Special session", "Closed", or null on an ordinary trading day. */
  note: string | null
  occasion: string | null
  exchanges: Partial<Record<string, ExchangeHours>>
  /** Null while the server's master contract is not loaded. */
  expiries: IndexExpiry[] | null
  stockExpiries: StockExpiry[] | null
  nextOpen: number | null
}

const ms = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
type Row = Record<string, unknown>

/** The rows of a list that read; null when it is not a list (the master is not loaded). */
function list<T>(value: unknown, read: (row: Row) => T | null): T[] | null {
  if (!Array.isArray(value)) return null
  return value.map((row) => read((row ?? {}) as Row)).filter((row): row is T => row !== null)
}

/** The server's answer, or null when it cannot be read. */
export function readDayFacts(data: unknown): DayFacts | null {
  const raw = (data ?? {}) as Row
  const day = text(raw.day)
  if (!day || typeof raw.trading_day !== 'boolean') return null
  const exchanges: DayFacts['exchanges'] = {}
  for (const [code, hours] of Object.entries((raw.exchanges ?? {}) as Record<string, Row>)) {
    const open = ms(hours?.open)
    const close = ms(hours?.close)
    if (open === null || close === null) continue
    exchanges[code] = {
      preOpen: ms(hours.pre_open),
      open,
      close,
      auctionUntil: ms(hours.auction_until),
    }
  }
  const when = (row: Row) => ({ exchange: text(row.exchange) ?? '', stopsAt: ms(row.stops_at) })
  return {
    day,
    tradingDay: raw.trading_day,
    note: text(raw.note),
    occasion: text(raw.occasion),
    exchanges,
    expiries: list(raw.expiries, (row) => {
      const [underlying, on] = [text(row.underlying), text(row.day)]
      return underlying && on ? { ...when(row), underlying, day: on } : null
    }),
    stockExpiries: list(raw.stock_expiries, (row) => {
      const on = text(row.day)
      const count = typeof row.count === 'number' && row.count > 0 ? row.count : null
      return on && count ? { ...when(row), day: on, count } : null
    }),
    nextOpen: ms(raw.next_open),
  }
}

const IST_HHMM = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const IST_WEEKDAY = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', weekday: 'short' })
const IST_DATE = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})
const IST_DAY = {
  /** "Tue 1 Dec", without the comma the locale puts after the weekday. */
  format: (at: number) =>
    IST_DATE.formatToParts(at)
      .filter((part) => part.type !== 'literal')
      .map((part) => part.value)
      .join(' '),
}

/** "09:15", as the clock in India shows it. */
export const hhmm = (at: number): string => IST_HHMM.format(at)
/** "Tue 17 Nov" for a day the server names (YYYY-MM-DD). */
export const dayWords = (day: string): string => IST_DAY.format(Date.parse(`${day}T06:00:00Z`))

/** The cash market's next open: today's until it has passed, then the next trading day's. */
export function nextOpenAt(facts: DayFacts, now: number): number | null {
  const nse = facts.exchanges.NSE
  return facts.tradingDay && nse && now < nse.open ? nse.open : facts.nextOpen
}

/** "Wed 09:15", or with the date when it is a week or more away: "Mon 23 Nov 09:15". */
export function whenWords(at: number, now: number): string {
  const day = at - now >= 6 * 86_400_000 ? IST_DAY.format(at) : IST_WEEKDAY.format(at)
  return `${day} ${hhmm(at)}`
}

export type Tone = 'neutral' | 'gold' | 'live'
export interface Chip {
  kind: string
  words: string
  tone: Tone
  icon: 'clock' | 'dot' | 'moon' | 'alert' | 'expiry'
}

/** The market chip: NSE's cash session today, from the calendar. */
export function marketChip(facts: DayFacts | null, failed: boolean, now: number): Chip {
  if (!facts) {
    return failed
      ? { kind: 'unread', words: 'Market hours not read', tone: 'neutral', icon: 'alert' }
      : { kind: 'reading', words: 'Reading market hours', tone: 'neutral', icon: 'clock' }
  }
  const nse = facts.exchanges.NSE
  const next = nextOpenAt(facts, now)
  const opens = next === null ? null : whenWords(next, now)
  const closed = (kind: string, head: string): Chip => ({
    kind,
    words: opens ? `${head} · opens ${opens}` : `${head} today`,
    tone: 'neutral',
    icon: 'moon',
  })
  if (!facts.tradingDay || !nse) {
    const head = facts.note === 'Weekend' || facts.note === 'Holiday' ? facts.note : 'Closed'
    return closed(head.toLowerCase(), head)
  }
  if (now < (nse.preOpen ?? nse.open)) {
    if (facts.note === 'Special session') {
      const words = `Special session ${hhmm(nse.open)}-${hhmm(nse.close)}`
      return { kind: 'special', words, tone: 'gold', icon: 'clock' }
    }
    return { kind: 'before', words: `Opens ${hhmm(nse.open)}`, tone: 'neutral', icon: 'clock' }
  }
  if (now < nse.open) {
    const words = `Pre-open · opens ${hhmm(nse.open)}`
    return { kind: 'pre_open', words, tone: 'gold', icon: 'clock' }
  }
  if (now < nse.close) {
    return { kind: 'open', words: `Open · closes ${hhmm(nse.close)}`, tone: 'live', icon: 'dot' }
  }
  if (nse.auctionUntil !== null && now < nse.auctionUntil) {
    const words = `Closing auction · price at about ${hhmm(nse.auctionUntil)}`
    return { kind: 'auction', words, tone: 'gold', icon: 'clock' }
  }
  // The cash market has closed and F&O still trades (to 15:40; from 7 Dec 2026 to 15:45).
  const fo = facts.exchanges.NFO
  if (fo && now < fo.close && fo.close > nse.close) {
    const words = `Cash closed · F&O to ${hhmm(fo.close)}`
    return { kind: 'fo', words, tone: 'gold', icon: 'clock' }
  }
  return closed('closed', 'Closed')
}

/** NIFTY, BANKNIFTY, SENSEX +2: the names once each, in the server's order. */
function names(expiries: IndexExpiry[]): string {
  const unique = [...new Set(expiries.map((each) => each.underlying))]
  const more = unique.length > 3 ? ` +${unique.length - 3}` : ''
  return unique.slice(0, 3).join(', ') + more
}

/**
 * The expiry chip: today's index expiry until its contracts stop; from the cash close the day
 * before (all day when today does not trade), the next trading day's. Null on other days.
 */
export function expiryChip(facts: DayFacts | null, now: number): Chip | null {
  if (!facts?.expiries?.length) return null
  const today = facts.expiries.filter(
    (each) => each.day === facts.day && (each.stopsAt === null || now < each.stopsAt)
  )
  if (today.length > 0) {
    const stops = today.map((each) => each.stopsAt).filter((at): at is number => at !== null)
    const stop = stops.length > 0 ? ` · stops ${hhmm(Math.min(...stops))}` : ''
    const words = `Expiry today: ${names(today)}${stop}`
    return { kind: 'expiry_today', words, tone: 'gold', icon: 'expiry' }
  }
  const later = facts.expiries.filter((each) => each.day > facts.day)
  const nse = facts.exchanges.NSE
  if (later.length === 0 || (facts.tradingDay && nse && now < nse.close)) return null
  const first = later.filter((each) => each.day === later[0].day)
  const tomorrow = Date.parse(`${first[0].day}T00:00:00Z`) - Date.parse(`${facts.day}T00:00:00Z`)
  const when =
    tomorrow === 86_400_000
      ? 'tomorrow'
      : IST_WEEKDAY.format(Date.parse(`${first[0].day}T06:00:00Z`))
  return {
    kind: 'expiry_next',
    words: `Expiry ${when}: ${names(first)}`,
    tone: 'neutral',
    icon: 'expiry',
  }
}

/** The next moment either chip changes its words, or null: the one timeout the row sets. */
export function nextEdge(facts: DayFacts | null, now: number): number | null {
  if (!facts) return null
  const moments = Object.values(facts.exchanges).flatMap((hours) =>
    hours ? [hours.preOpen, hours.open, hours.close, hours.auctionUntil] : []
  )
  moments.push(...(facts.expiries ?? []).map((each) => each.stopsAt))
  const later = moments.filter((at): at is number => at !== null && at > now)
  return later.length > 0 ? Math.min(...later) : null
}
