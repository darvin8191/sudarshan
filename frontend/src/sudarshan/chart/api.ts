/**
 * The few requests the phone chart makes outside the chart itself: the key
 * and stream address every chart uses, an instrument's contract details, the
 * timeframes the broker offers, and symbol search. Each resolves to null or
 * an empty list on failure, and the page says what that means; none of them
 * throws.
 */
import { type IntervalData, type IntervalGroup, intervalGroups } from '@/lib/trading/intervals'
import type { SearchRow } from '@/lib/trading/terminal'

export interface FeedConfig {
  apiKey: string
  wsUrl: string
}

/** The API key and stream address, as the /trading page reads them. */
export async function loadFeedConfig(): Promise<FeedConfig | null> {
  try {
    const [keyRes, cfgRes] = await Promise.all([
      fetch('/api/websocket/apikey', { credentials: 'same-origin' }).then((r) => r.json()),
      fetch('/api/websocket/config', { credentials: 'same-origin' }).then((r) => r.json()),
    ])
    if (keyRes?.status !== 'success' || typeof keyRes.api_key !== 'string' || !keyRes.api_key)
      return null
    const wsUrl =
      typeof cfgRes?.websocket_url === 'string' && cfgRes.websocket_url
        ? cfgRes.websocket_url
        : 'ws://127.0.0.1:8765'
    return { apiKey: keyRes.api_key, wsUrl }
  } catch {
    return null
  }
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T | null> {
  try {
    const res = await fetch(`/api/v1/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const json = (await res.json()) as { status?: string; data?: T }
    return res.ok && json.status !== 'error' && json.data !== undefined ? json.data : null
  } catch {
    return null
  }
}

/** The contract facts an order ticket needs. */
export interface InstrumentInfo {
  name: string
  lotSize: number
  tickSize: number
}

export async function loadInstrument(
  apiKey: string,
  symbol: string,
  exchange: string
): Promise<InstrumentInfo | null> {
  const data = await post<Record<string, unknown>>('symbol', { apikey: apiKey, symbol, exchange })
  if (!data) return null
  return {
    name: typeof data.name === 'string' ? data.name : '',
    lotSize: Number(data.lotsize) > 0 ? Number(data.lotsize) : 1,
    tickSize: Number(data.tick_size) > 0 ? Number(data.tick_size) : 0.05,
  }
}

/** The timeframes the broker offers, grouped as the terminal groups them; null when not read. */
export async function loadIntervals(apiKey: string): Promise<IntervalGroup[] | null> {
  const data = await post<IntervalData>('intervals', { apikey: apiKey })
  const groups = data ? intervalGroups(data) : []
  return groups.length ? groups : null
}

/** The date in India of an instant, YYYY-MM-DD. */
const indiaDay = (ms: number) => new Date(ms + 19_800_000).toISOString().slice(0, 10)

/**
 * A contract's open interest at the last close before today (C9), for the
 * header's change, from daily history: read once when the chart opens, never
 * on a timer. Null when history carries none.
 */
export async function loadPreviousOi(
  apiKey: string,
  symbol: string,
  exchange: string,
  now = Date.now()
): Promise<number | null> {
  const rows = await post<Array<Record<string, unknown>>>('history', {
    apikey: apiKey,
    symbol,
    exchange,
    interval: 'D',
    start_date: indiaDay(now - 10 * 86_400_000),
    end_date: indiaDay(now),
  })
  if (!Array.isArray(rows)) return null
  const today = indiaDay(now)
  for (let i = rows.length - 1; i >= 0; i--) {
    const stamp = Number(rows[i].timestamp ?? rows[i].time)
    const oi = Number(rows[i].oi)
    if (!Number.isFinite(stamp) || !(oi > 0)) continue
    if (indiaDay(stamp > 1e12 ? stamp : stamp * 1000) < today) return oi
  }
  return null
}

/** Broker symbols matching what was typed, for the shared search dialog. */
export async function searchSymbols(
  apiKey: string,
  query: string,
  exchange?: string,
  limit = 30
): Promise<SearchRow[]> {
  const rows = await post<SearchRow[]>('search', {
    apikey: apiKey,
    query,
    ...(exchange ? { exchange } : {}),
  })
  return Array.isArray(rows) ? rows.slice(0, limit) : []
}
