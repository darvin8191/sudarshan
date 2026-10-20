/**
 * The few requests the phone chart makes outside the chart itself: the key
 * and stream address every chart uses, an instrument's contract details, and
 * symbol search. Each resolves to null or an empty list on failure, and the
 * page says what that means; none of them throws.
 */
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
