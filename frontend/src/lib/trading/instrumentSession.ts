/**
 * SUDARSHAN (v0.8.1 review M1, M2, M3): an instrument's own session facts for
 * today, from the server (GET /sudarshan/session/instrument, which reads
 * sudarshan/instrument_session.py). The chart's calendar is the exchange's;
 * this adds what is one instrument's own, and the chart never works it out:
 *
 * - the window after the cash close in which a closing print belongs to the
 *   day's last candle (from 7 Dec 2026, a stock with futures and an index);
 * - when an expiring contract stops trading on its expiry day (15:30 for an
 *   index contract from 7 Dec 2026).
 *
 * Asked once per instrument per day in India. A failure leaves the chart on
 * the exchange's hours, as it was, and is asked again after a few minutes. A
 * page left open past midnight asks again for the new day (`dayWatch.ts`,
 * review N3); only today's facts and the previous day's are kept.
 */
import { istDay } from './dayWatch'
import type { ClosingWindow } from './liveBars'

export interface InstrumentSession {
  /** The day in India the facts are for. */
  day: string
  /** Epoch milliseconds an expiring contract stops trading today, or null. */
  stopsAt: number | null
  /** UTC seconds: a print stamped inside belongs to the day's last candle. */
  closing: ClosingWindow | null
}

const TIMEOUT_MS = 8_000
/** A failed request is asked again no sooner than this. */
const RETRY_AFTER_MS = 5 * 60_000

interface Entry {
  /** The day in India the facts are for (the key's last part). */
  day: string
  at: number
  value: Promise<InstrumentSession | null>
  failed: boolean
}
const book = new Map<string, Entry>()

function epoch(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

/** The facts as the server sends them, or null when the answer cannot be read. */
export function readInstrumentSession(data: unknown): InstrumentSession | null {
  if (!data || typeof data !== 'object') return null
  const row = data as { day?: unknown; stops_at?: unknown; closing?: unknown }
  if (typeof row.day !== 'string') return null
  const closing = row.closing as { from?: unknown; until?: unknown } | null | undefined
  const from = epoch(closing?.from)
  const until = epoch(closing?.until)
  return {
    day: row.day,
    stopsAt: epoch(row.stops_at),
    closing:
      from !== null && until !== null && until > from
        ? { from: from / 1000, until: until / 1000 }
        : null,
  }
}

async function load(symbol: string, exchange: string): Promise<InstrumentSession | null> {
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), TIMEOUT_MS)
  try {
    const query = new URLSearchParams({ symbol, exchange })
    const res = await fetch(`/sudarshan/session/instrument?${query}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: abort.signal,
    })
    const body = (await res.json()) as { status?: string; data?: unknown }
    return res.ok && body?.status === 'success' ? readInstrumentSession(body.data) : null
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

/** Forget the facts of any day but today and the day before: a page open for weeks keeps no more. */
function prune(now: number): void {
  const keep = new Set([istDay(now), istDay(now - 86_400_000)])
  for (const [key, entry] of book) if (!keep.has(entry.day)) book.delete(key)
}

/** How many days' facts are held. For tests. */
export function instrumentSessionDays(): string[] {
  return [...new Set([...book.values()].map((entry) => entry.day))].sort()
}

/** Today's facts for an instrument, asked once per day; null when they cannot be had. */
export function instrumentSessionFor(
  symbol: string,
  exchange: string,
  now = Date.now()
): Promise<InstrumentSession | null> {
  if (!symbol || !exchange) return Promise.resolve(null)
  const day = istDay(now)
  const key = `${exchange}:${symbol}:${day}`
  const kept = book.get(key)
  if (kept && !(kept.failed && now - kept.at >= RETRY_AFTER_MS)) return kept.value
  prune(now)
  const entry: Entry = { day, at: now, value: load(symbol, exchange), failed: false }
  entry.value = entry.value.then((found) => {
    entry.failed = found === null
    return found
  })
  book.set(key, entry)
  return entry.value
}

/** Forget every instrument's facts. For tests. */
export function resetInstrumentSessions(): void {
  book.clear()
}
