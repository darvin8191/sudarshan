/**
 * What the phone chart remembers on this device: the last timeframe chosen
 * and the last instrument opened. Browser storage only, read defensively: a
 * private window or blocked storage gives the defaults, never an error.
 */

/** The timeframes the phone chart offers, in the order they are shown. */
export const CHART_CHIPS = ['1m', '3m', '5m', '15m', '1h', '1D'] as const
export type ChartChip = (typeof CHART_CHIPS)[number]

export const DEFAULT_CHIP: ChartChip = '5m'

/** What a bare /chart opens when nothing was opened on this device before. */
export const DEFAULT_INSTRUMENT = { symbol: 'NIFTY', exchange: 'NSE_INDEX' } as const

const CHIP_KEY = 'sudarshan-chart-interval'
const LAST_KEY = 'sudarshan-chart-last'

type Store = Pick<Storage, 'getItem' | 'setItem'>

function store(): Store | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function isChartChip(value: unknown): value is ChartChip {
  return typeof value === 'string' && (CHART_CHIPS as readonly string[]).includes(value)
}

/** The timeframe last chosen on this device. */
export function readChip(storage: Store | null = store()): ChartChip {
  try {
    const saved = storage?.getItem(CHIP_KEY)
    return isChartChip(saved) ? saved : DEFAULT_CHIP
  } catch {
    return DEFAULT_CHIP
  }
}

export function saveChip(chip: ChartChip, storage: Store | null = store()): void {
  try {
    storage?.setItem(CHIP_KEY, chip)
  } catch {
    /* kept for this visit only */
  }
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
