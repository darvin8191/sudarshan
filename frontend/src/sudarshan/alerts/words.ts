/**
 * The alert form's arithmetic and words (M10.3), free of React. The engine counts every price
 * and level in paise (sudarshan/alerts/market/grain.py), so the form does too, with integers
 * only: a typed level becomes its upward and downward edges exactly (Decimal on the server,
 * BigInt here), and a feed price becomes paise by rounding half to even, as Python's round
 * does. The snap group of sudarshan/alerts/tests/market_vectors.json pins both sides.
 *
 * "Checks at" says where a level off the instrument's tick will be met, as the server's
 * words.checks_at says it on the saved alert; on the tick the form still shows the level as it
 * will be read ("Checks at ₹1,399.80" for "1,399.8").
 */
import type { AlertExchange, AlertKind, Candle, Frequency, PriceOp } from '@/api/marketAlerts'
import { formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { isIndex } from '@/sudarshan/watchlist/rows'

interface Exact {
  /** The digits, without the point. */
  units: bigint
  /** How many of them follow the point. */
  scale: number
}

/** A typed number: digits with an optional decimal part, Indian commas allowed. */
function exact(text: string | null | undefined): Exact | null {
  const clean = String(text ?? '')
    .trim()
    .replace(/,/g, '')
  const match = /^(\d*)(?:\.(\d*))?$/.exec(clean)
  if (!match || `${match[1]}${match[2] ?? ''}` === '') return null
  const fraction = match[2] ?? ''
  return { units: BigInt(`${match[1] || '0'}${fraction}`), scale: fraction.length }
}

/**
 * Whether typed text is a plain number the form may send: ASCII digits, Indian commas and at
 * most one '.', nothing else (no exponent, no '_', no digits of another script), or nothing at
 * all, which the server answers for. The server reads levels just as strictly.
 */
export function plainNumber(text: string): boolean {
  const clean = text.trim().replace(/,/g, '')
  return clean === '' || (/^[0-9]*\.?[0-9]*$/.test(clean) && /[0-9]/.test(clean))
}

const TEN = BigInt(10)
const HUNDRED = BigInt(100)
const ZERO = BigInt(0)
const ONE = BigInt(1)

const pow10 = (scale: number) => TEN ** BigInt(scale)
/** n / d rounded up, for n >= 0 and d > 0. */
const up = (n: bigint, d: bigint) => (n % d === ZERO ? n / d : n / d + ONE)

/** A level's edges in paise: the first paisa at or above it, the last at or below it. */
export function levelEdges(level: string): { up: number; down: number } | null {
  const typed = exact(level)
  if (!typed || typed.units === ZERO) return null
  const scaled = typed.units * HUNDRED
  const d = pow10(typed.scale)
  return { up: Number(up(scaled, d)), down: Number(scaled / d) }
}

/**
 * Where a level off the instrument's tick is met, in paise: the first price on the tick at or
 * above it and the last at or below it. Null on the tick, or when either is not a number.
 */
export function tickEdges(level: string, tick: string | null): [number, number] | null {
  const typed = exact(level)
  const step = exact(tick ?? '0.01')
  if (!typed || !step || step.units === ZERO) return null
  const n = typed.units * pow10(step.scale)
  const d = step.units * pow10(typed.scale)
  const above = up(n, d)
  const below = n / d
  if (above === below) return null
  const paise = (k: bigint) => Number(up(k * step.units * HUNDRED, pow10(step.scale)))
  return [paise(above), paise(below)]
}

/** Python's round(): half to even. */
function roundHalfEven(x: number): number {
  const floor = Math.floor(x)
  const rest = x - floor
  if (rest > 0.5) return floor + 1
  if (rest < 0.5) return floor
  return floor % 2 === 0 ? floor : floor + 1
}

/** A feed price in paise, as the engine reads it; null unless a finite price of a paisa or more. */
export function paiseOf(price: unknown): number | null {
  if (typeof price !== 'number' || !(price > 0) || !Number.isFinite(price)) return null
  const count = roundHalfEven(price * 100)
  return count > 0 ? count : null
}

/** Paise as the price's text with two decimals: 10005 is "100.05". */
export function paiseText(paise: number): string {
  const whole = Math.trunc(paise / 100)
  return `${whole}.${String(paise % 100).padStart(2, '0')}`
}

/** A live price as the form's level: on the paisa, "812.40". */
export function levelFrom(price: unknown): string {
  const paise = paiseOf(price)
  return paise === null ? '' : paiseText(paise)
}

/** Paise as the instrument is quoted: ₹1,428.25, or 25,903.15 points for an index. */
export function priced(exchange: string, paise: number): string {
  return isIndex(exchange) ? formatPrice(paise / 100) : formatRupees(paise / 100)
}

type Way = 'up' | 'down' | 'either'

export function wayOfOp(op: PriceOp): Way {
  if (op === 'crosses_above' || op === 'at_or_above') return 'up'
  if (op === 'crosses_below' || op === 'at_or_below') return 'down'
  return 'either'
}

/**
 * "Checks at ₹100.05 (nearest tick)"; on the tick, or with the tick not known (a contract still
 * being read), the level as it will be read.
 */
export function checksAtWords(
  exchange: string,
  op: PriceOp,
  level: string,
  tick: string | null
): string | null {
  const edges = levelEdges(level)
  if (!edges) return null
  const snapped = tick === null ? null : tickEdges(level, tick)
  const way = wayOfOp(op)
  if (!snapped) return `Checks at ${priced(exchange, way === 'down' ? edges.down : edges.up)}`
  const [above, below] = snapped.map((paise) => priced(exchange, paise))
  if (way === 'up') return `Checks at ${above} (nearest tick)`
  if (way === 'down') return `Checks at ${below} (nearest tick)`
  return `Checks at ${above} going up and ${below} going down (nearest ticks)`
}

export const OP_WORDS: Record<PriceOp, string> = {
  crosses_above: 'Crosses above',
  crosses_below: 'Crosses below',
  crosses: 'Crosses',
  at_or_above: 'Is at or above',
  at_or_below: 'Is at or below',
}

/**
 * What the price already says about a price alert as it would be saved: a crossing that would
 * wait for the price to come back first (a warning), or a level check that fires at once.
 */
export function priceNote(
  name: string,
  exchange: string,
  op: PriceOp,
  level: string,
  price: number | null
): { warn: boolean; text: string } | null {
  const edges = levelEdges(level)
  const now = paiseOf(price)
  if (!edges || now === null) return null
  const above = priced(exchange, edges.up)
  const below = priced(exchange, edges.down)
  if (op === 'crosses_above' && now >= edges.up) {
    return {
      warn: true,
      text: `${name} is already at or above ${above}. 'Crosses above' waits for it to fall below and come back; 'Is at or above' fires at once.`,
    }
  }
  if (op === 'crosses_below' && now <= edges.down) {
    return {
      warn: true,
      text: `${name} is already at or below ${below}. 'Crosses below' waits for it to rise above and come back; 'Is at or below' fires at once.`,
    }
  }
  if (op === 'at_or_above' && now >= edges.up) {
    return { warn: false, text: `${name} is at or above ${above} now, so this fires at once.` }
  }
  if (op === 'at_or_below' && now <= edges.down) {
    return { warn: false, text: `${name} is at or below ${below} now, so this fires at once.` }
  }
  return null
}

/** What a kind starts with when the form leaves a field out (contract 1.3). */
export const KIND_DEFAULTS: Record<
  AlertKind,
  { frequency: Frequency; candle: Candle; cooldown: number }
> = {
  price: { frequency: 'once', candle: 'day', cooldown: 0 },
  move: { frequency: 'per_candle', candle: 'day', cooldown: 0 },
  day_break: { frequency: 'per_candle', candle: 'day', cooldown: 0 },
  vwap: { frequency: 'every_time', candle: 'day', cooldown: 900 },
}

/** The exchanges market alerts cover (contract 1.2). */
export const COVERED = new Set<string>(['NSE', 'BSE', 'NFO', 'BFO', 'NSE_INDEX', 'BSE_INDEX'])

export function isCovered(exchange: string): exchange is AlertExchange {
  return COVERED.has(exchange.toUpperCase())
}

/** A derivative runs to its expiry unless told otherwise; cash and indices for 60 days. */
export function isDerivative(exchange: string): boolean {
  const venue = exchange.toUpperCase()
  return venue === 'NFO' || venue === 'BFO'
}

/** Why a choice is off for this instrument, in the server's own sentences (spec.py). */
export const NOT_FOR = {
  indexVwap: 'An index has no traded volume, so it has no VWAP. Use its future.',
  indexQuotes: 'An index has no bid or ask. Watch its last price instead.',
  breakAtClose:
    "A day high or low alert checks every trade, so 'At candle close' is not available for it.",
  quoteAtClose:
    "'At candle close' judges the last trade price. For the bid or ask, pick another frequency.",
  exchange: 'Market alerts work on NSE, BSE, NFO and BFO instruments and their indices for now.',
} as const

/** The form's own sentences for text that is not a plain number (`plainNumber`). */
export const NOT_PLAIN = {
  level: "Type the level in plain digits, with one '.' before any decimals, like 25900 or 100.05.",
  percent: "Type the move in plain digits, with one '.' before any decimals, like 2 or 1.5.",
} as const

/** The tick an instrument is checked on: an index moves in hundredths. */
export function tickOf(exchange: string, tick: unknown): string | null {
  if (isIndex(exchange)) return '0.01'
  const text = typeof tick === 'number' ? String(tick) : typeof tick === 'string' ? tick : ''
  return exact(text) && Number(text) > 0 ? text : null
}
