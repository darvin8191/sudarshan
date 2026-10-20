/**
 * The two instrument look-ups the watchlist makes besides the lists and the
 * prices, both on the app's existing `/api/v1` endpoints and the same master
 * contract the /trading terminal searches.
 */
import { apiClient } from '@/api/client'

/** One row of `POST /api/v1/search`. */
export interface SearchResult {
  symbol: string
  exchange: string
  name?: string
  instrumenttype?: string
  lotsize?: number | string | null
  tick_size?: number | string | null
}

interface Answer<T> {
  status: 'success' | 'error'
  message?: string
  data?: T
}

/** Instruments matching what was typed, most relevant first (the server ranks them). */
export async function searchSymbols(apiKey: string, query: string): Promise<SearchResult[]> {
  const response = await apiClient.post<Answer<SearchResult[]>>('/search', {
    apikey: apiKey,
    query,
  })
  if (response.data.status !== 'success') throw new Error(response.data.message || 'Search failed')
  return response.data.data ?? []
}

/** What an order ticket needs to know about a contract. */
export interface Contract {
  lotSize: number
  tickSize: number
}

function positive(value: unknown): number | null {
  const n = typeof value === 'string' ? Number(value) : value
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null
}

/** The lot and tick size of one instrument (`POST /api/v1/symbol`), from the master contract. */
export async function contractOf(
  apiKey: string,
  symbol: string,
  exchange: string
): Promise<Contract> {
  const response = await apiClient.post<Answer<SearchResult>>('/symbol', {
    apikey: apiKey,
    symbol,
    exchange,
  })
  if (response.data.status !== 'success' || !response.data.data) {
    throw new Error(response.data.message || 'Contract not found')
  }
  return {
    lotSize: positive(response.data.data.lotsize) ?? 1,
    tickSize: positive(response.data.data.tick_size) ?? 0.05,
  }
}
