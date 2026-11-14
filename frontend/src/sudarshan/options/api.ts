/**
 * The options screen's reads (M11.1, O4): the chain bridge (`blueprints/sudarshan_options.py`), and
 * upstream's own chain once as the fallback when the bridge's first read fails. Every answer is a
 * state with the server's sentence; a problem with no answer gets words of the page's own, never a
 * status code or an address.
 */
import axios from 'axios'
import { webClient } from '@/api/client'
import { optionChainApi } from '@/api/option-chain'
import type { OptionData } from '@/types/option-chain'
import type { ChainSnapshot, SnapLeg } from './model'

export type ReadState = 'ok' | 'signin' | 'wait' | 'unavailable' | 'none'

/** The chain as the bridge sends it: the snapshot and what it is of. */
export interface ServerChain extends ChainSnapshot {
  exchange: string
  underlying: string
  underlying_exchange: string
  pcr: number | null
  /** Legs Upstox named that today's master lacks. */
  left_out: number
  by_strike: number
}

export interface ChainAnswer {
  state: ReadState
  words: string | null
  chain: ServerChain | null
}

export interface Listing {
  underlying: string
  exchange: string
  spot: { symbol: string; exchange: string }
  future: { symbol: string; exchange: string; expiry: string } | null
  expiries: Array<{ date: string; stops_at: number }>
  /** The server's clock when it answered, epoch ms. */
  now: number
}

export interface ListingAnswer {
  state: ReadState
  words: string | null
  listing: Listing | null
}

export const NO_ANSWER = 'No answer came from the server. Check the connection, then try again.'
const SIGNED_OUT = 'You are signed out. Sign in again to read option chains.'
const WAIT_MS = 15_000

function refusal(error: unknown): { state: ReadState; words: string } {
  if (axios.isCancel(error)) throw error
  const response = axios.isAxiosError(error) ? error.response : undefined
  const message = (response?.data as { message?: unknown } | undefined)?.message
  const said = typeof message === 'string' && message.trim() ? message.trim() : null
  if (response?.status === 401 || response?.status === 403)
    return { state: 'signin', words: said ?? SIGNED_OUT }
  if (response?.status === 429) return { state: 'wait', words: said ?? NO_ANSWER }
  return { state: 'unavailable', words: said ?? NO_ANSWER }
}

async function ask<T extends { state: ReadState; words: string | null }>(
  path: string,
  params: Record<string, string>,
  empty: Omit<T, 'state' | 'words'>,
  signal?: AbortSignal
): Promise<T> {
  try {
    const answer = await webClient.get(`/sudarshan/options/api/${path}`, {
      params,
      timeout: WAIT_MS,
      signal,
    })
    const data = (answer.data as { status?: string; data?: T } | null)?.data
    if (answer.data?.status === 'success' && data && typeof data.state === 'string') return data
    return { ...empty, state: 'unavailable', words: NO_ANSWER } as T
  } catch (error) {
    return { ...empty, ...refusal(error) } as T
  }
}

/** One expiry's chain. Never throws but for a cancelled request. */
export function readChain(
  exchange: string,
  underlying: string,
  expiry: string,
  signal?: AbortSignal
): Promise<ChainAnswer> {
  return ask<ChainAnswer>('chain', { exchange, underlying, expiry }, { chain: null }, signal)
}

/** An underlying's expiries that still trade, its spot and its nearest future. */
export function readListing(
  exchange: string,
  underlying: string,
  signal?: AbortSignal
): Promise<ListingAnswer> {
  return ask<ListingAnswer>('expiries', { exchange, underlying }, { listing: null }, signal)
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

/** "2026-11-17" as upstream's chain takes it: "17NOV26". */
export function upstreamExpiry(iso: string): string {
  const [year, month, day] = iso.split('-')
  return `${day}${MONTHS[Number(month) - 1]}${year.slice(2)}`
}

const figure = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined

/**
 * Upstream's own chain (`/api/v1/optionchain`), read once when the bridge's first read fails: about
 * twenty strikes either side of the money, with no previous open interest, so OI change then reads
 * "not available". Null when it cannot be read either.
 */
export async function readFallback(
  apiKey: string,
  listing: Listing,
  expiry: string,
  stopsAt: number,
  now: number
): Promise<ChainSnapshot | null> {
  try {
    const answer = await optionChainApi.getOptionChain(
      apiKey,
      listing.underlying,
      listing.spot.exchange,
      upstreamExpiry(expiry),
      20
    )
    if (answer.status !== 'success' || !Array.isArray(answer.chain)) return null
    const leg = (raw: OptionData | null): SnapLeg | undefined => {
      if (!raw?.symbol) return undefined
      return {
        symbol: raw.symbol,
        exchange: listing.exchange,
        lot: figure(raw.lotsize) ?? null,
        tick: figure(raw.tick_size) ?? null,
        ltp: figure(raw.ltp),
        bid: figure(raw.bid),
        ask: figure(raw.ask),
        oi: typeof raw.oi === 'number' && Number.isFinite(raw.oi) ? raw.oi : undefined,
        volume: figure(raw.volume),
        close: figure(raw.prev_close),
      }
    }
    return {
      as_of: now,
      spot: figure(answer.underlying_ltp) ?? null,
      expiry,
      expiry_ts: stopsAt,
      strikes: answer.chain
        .map((row) => ({ strike: row.strike, ce: leg(row.ce), pe: leg(row.pe) }))
        .sort((a, b) => a.strike - b.strike),
    }
  } catch {
    return null
  }
}
