/**
 * How far an alert stands from firing, from the page's own tick stream (M10.3): the figure the
 * alert watches and its distance from what the alert checks. Display only: the engine decides
 * on the server, in paise, from its own feed; this never says whether an alert will fire.
 */
import type {
  AlertRow,
  BreakParams,
  LineCode,
  MarketAlertsState,
  MoveParams,
  PriceParams,
  Segment,
} from '@/api/marketAlerts'
import type { MarketData } from '@/lib/MarketDataManager'
import { formatPercent, formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { isIndex } from '@/sudarshan/watchlist/rows'

export interface Measure {
  /** The figure the alert watches: 'LTP', 'Bid' or 'Ask'. */
  label: string
  value: number | null
  /** How far it stands from what the alert checks: '₹2.30 below the level', '+1.24% on the day'. */
  distance: string | null
}

/** Whether an alert's figures come only with the Quote stream: bid, ask, open, high, low, VWAP. */
export function needsQuote(row: Pick<AlertRow, 'kind' | 'params'>): boolean {
  if (row.kind === 'price') return ((row.params as PriceParams).source ?? 'ltp') !== 'ltp'
  if (row.kind === 'move') return (row.params as MoveParams).base === 'open'
  return true
}

const OPEN = new Set<LineCode>(['ok', 'closing_auction', 'feed_down'])
const SHUT = new Set<LineCode>([
  'outside_hours',
  'closed_today',
  'closed_by_exchange',
  'not_open_yet',
])

/**
 * Whether an alert's market is open, from its segment's line on the server (keyed by the
 * instrument key's prefix): the page never works out a session itself. Null when not known.
 */
export function segmentOpen(
  segments: MarketAlertsState['segments'] | undefined,
  row: Pick<AlertRow, 'instrument_key'>
): boolean | null {
  const code = segments?.[row.instrument_key.split('|')[0] as Segment]?.code
  if (code === undefined) return null
  return OPEN.has(code) ? true : SHUT.has(code) ? false : null
}

function price(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

function typed(level: string): number | null {
  return price(Number(String(level).replace(/,/g, '')))
}

/** A price as the instrument is quoted: rupees, or index points. */
export function money(row: Pick<AlertRow, 'exchange'>, value: number): string {
  return isIndex(row.exchange) ? formatPrice(value) : formatRupees(value)
}

export function measure(row: AlertRow, data: MarketData | undefined): Measure {
  const ltp = price(data?.ltp)
  // To the paisa, so float noise never reads as a gap: 25,903.15 against 25,900 is 3.15.
  const apart = (value: number, mark: number, what: string) => {
    const gap = Math.round((value - mark) * 100) / 100
    if (gap === 0) return `at ${what}`
    return `${money(row, Math.abs(gap))} ${gap > 0 ? 'above' : 'below'} ${what}`
  }
  switch (row.kind) {
    case 'price': {
      const { source = 'ltp', level } = row.params as PriceParams
      const value =
        source === 'bid' ? price(data?.bid_price) : source === 'ask' ? price(data?.ask_price) : ltp
      const mark = typed(level)
      const label = source === 'bid' ? 'Bid' : source === 'ask' ? 'Ask' : 'LTP'
      return {
        label,
        value,
        distance: value !== null && mark !== null ? apart(value, mark, 'the level') : null,
      }
    }
    case 'move': {
      const { base = 'close' } = row.params as MoveParams
      const from =
        base === 'open' ? price(data?.open) : (price(data?.prev_close) ?? price(data?.cp))
      if (ltp === null || from === null) return { label: 'LTP', value: ltp, distance: null }
      const words = base === 'open' ? 'from today’s open' : 'on the day'
      return {
        label: 'LTP',
        value: ltp,
        distance: `${formatPercent(((ltp - from) / from) * 100)} ${words}`,
      }
    }
    case 'day_break': {
      const { side } = row.params as BreakParams
      const mark = price(side === 'low' ? data?.low : data?.high)
      const what = side === 'low' ? 'today’s low' : 'today’s high'
      return {
        label: 'LTP',
        value: ltp,
        distance: ltp !== null && mark !== null ? apart(ltp, mark, what) : null,
      }
    }
    default: {
      const vwap = price(data?.average_price)
      return {
        label: 'LTP',
        value: ltp,
        distance: ltp !== null && vwap !== null ? apart(ltp, vwap, 'VWAP') : null,
      }
    }
  }
}
