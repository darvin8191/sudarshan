/**
 * What the phone chart remembers on this device: the timeframe last chosen,
 * the timeframes pinned above the chart, and the last instrument opened.
 * Browser storage only, read defensively: a private window or blocked
 * storage gives the defaults, never an error.
 *
 * A timeframe is the broker's own name for it, as `/api/v1/intervals` lists
 * them: 1m, 15m, 1h, D, W, M. Its chip reads 1D, 1W and 1M for the last three.
 */
import { intervalSeconds } from '@/lib/trading/intervals'

/** What is pinned on a phone that has pinned nothing yet, short to long. */
export const DEFAULT_PINNED: readonly string[] = ['1m', '3m', '5m', '15m', '1h', 'D']

/** Six pinned chips and More fit a 360 px phone at 48 dp a tap. */
export const MAX_PINNED = 6

export const DEFAULT_INTERVAL = '5m'

/** What a bare /chart opens when nothing was opened on this device before. */
export const DEFAULT_INSTRUMENT = { symbol: 'NIFTY', exchange: 'NSE_INDEX' } as const

const INTERVAL_KEY = 'sudarshan-chart-interval'
const PINNED_KEY = 'sudarshan-chart-pinned'
const LAST_KEY = 'sudarshan-chart-last'

type Store = Pick<Storage, 'getItem' | 'setItem'>

function store(): Store | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

/** A timeframe as a broker names one: 30s, 1m, 15m, 1h, 4h, D, W or M. */
export function isInterval(value: unknown): value is string {
  return typeof value === 'string' && /^(?:[1-9]\d{0,2}[smh]|[DWM])$/.test(value)
}

/** The broker's name for a saved timeframe, or null; an older build saved the day as 1D. */
export function normalizeInterval(value: unknown): string | null {
  if (value === '1D') return 'D'
  return isInterval(value) ? value : null
}

/** A timeframe's chip: 1m, 1h, and 1D, 1W, 1M for a day, a week and a month. */
export function intervalLabel(interval: string): string {
  return interval === 'D' || interval === 'W' || interval === 'M' ? `1${interval}` : interval
}

const UNITS: Record<string, [string, string]> = {
  s: ['second', 'seconds'],
  m: ['minute', 'minutes'],
  h: ['hour', 'hours'],
}
const CALENDAR: Record<string, string> = { D: '1 day', W: '1 week', M: '1 month' }

/** A timeframe in words: 1 minute, 15 minutes, 1 day. */
export function intervalName(interval: string): string {
  const parts = /^(\d+)([smh])$/.exec(interval)
  if (!parts) return CALENDAR[interval] ?? interval
  const n = Number(parts[1])
  const [one, many] = UNITS[parts[2]]
  return `${n} ${n === 1 ? one : many}`
}

/** How long a timeframe's candle is, in seconds, to order chips short to long. */
export function intervalSpan(interval: string): number {
  const seconds = intervalSeconds(interval)
  if (seconds !== null) return seconds
  return { D: 86_400, W: 604_800, M: 2_678_400 }[interval] ?? Number.MAX_SAFE_INTEGER
}

/** A day, a week or a month: its candles are stamped with a date, not a time. */
export function isCalendarInterval(interval: string): boolean {
  return interval === 'D' || interval === 'W' || interval === 'M'
}

/** The timeframe last chosen on this device. */
export function readInterval(storage: Store | null = store()): string {
  try {
    return normalizeInterval(storage?.getItem(INTERVAL_KEY)) ?? DEFAULT_INTERVAL
  } catch {
    return DEFAULT_INTERVAL
  }
}

export function saveInterval(interval: string, storage: Store | null = store()): void {
  try {
    storage?.setItem(INTERVAL_KEY, interval)
  } catch {
    /* kept for this visit only */
  }
}

/** Short to long, each once. */
function ordered(intervals: readonly string[]): string[] {
  return [...new Set(intervals)].sort((a, b) => intervalSpan(a) - intervalSpan(b))
}

/** The timeframes pinned above the chart on this device, short to long. */
export function readPinned(storage: Store | null = store()): string[] {
  try {
    const saved: unknown = JSON.parse(storage?.getItem(PINNED_KEY) ?? 'null')
    if (Array.isArray(saved)) {
      const kept = ordered(saved.map(normalizeInterval).filter((v): v is string => v !== null))
      if (kept.length) return kept.slice(0, MAX_PINNED)
    }
  } catch {
    /* fall through to the default */
  }
  return [...DEFAULT_PINNED]
}

export function savePinned(pinned: readonly string[], storage: Store | null = store()): void {
  try {
    storage?.setItem(PINNED_KEY, JSON.stringify(pinned))
  } catch {
    /* kept for this visit only */
  }
}

/**
 * Pin a timeframe, or unpin it. Refused, with the list unchanged, when six are
 * pinned already (`full`) or when it is the last one pinned (`last`).
 */
export function togglePin(
  pinned: readonly string[],
  interval: string
): { pinned: string[]; refused?: 'full' | 'last' } {
  if (pinned.includes(interval)) {
    if (pinned.length <= 1) return { pinned: [...pinned], refused: 'last' }
    return { pinned: pinned.filter((p) => p !== interval) }
  }
  if (pinned.length >= MAX_PINNED) return { pinned: [...pinned], refused: 'full' }
  return { pinned: ordered([...pinned, interval]) }
}

/** The instrument last opened on this device, or the default. */
export function readLastInstrument(storage: Store | null = store()): {
  symbol: string
  exchange: string
} {
  try {
    const saved = JSON.parse(storage?.getItem(LAST_KEY) ?? 'null') as {
      symbol?: unknown
      exchange?: unknown
    } | null
    if (
      saved &&
      typeof saved.symbol === 'string' &&
      saved.symbol.trim() &&
      typeof saved.exchange === 'string' &&
      saved.exchange.trim()
    )
      return { symbol: saved.symbol, exchange: saved.exchange }
  } catch {
    /* fall through to the default */
  }
  return { ...DEFAULT_INSTRUMENT }
}

export function saveLastInstrument(
  symbol: string,
  exchange: string,
  storage: Store | null = store()
): void {
  try {
    storage?.setItem(LAST_KEY, JSON.stringify({ symbol, exchange }))
  } catch {
    /* kept for this visit only */
  }
}

/** The phone chart's path for an instrument. */
export function chartPath(symbol: string, exchange: string): string {
  return `/chart/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}`
}
