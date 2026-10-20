import { useCallback, useEffect, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { keyOf } from '@/sudarshan/dashboard/live'
import type { Snapshot } from './rows'

/** A quote read less than this long ago is not read again when a list opens or grows. */
export const SNAPSHOT_FRESH_MS = 15_000

/** After the feed reconnects, quotes older than this are read again. */
export const RECONNECT_REREAD_MS = 5_000

/** Back on the page after more than this long away, every quote is read again. */
export const AWAY_MS = 15_000

export interface Instrument {
  symbol: string
  exchange: string
}

export type SnapshotStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface QuoteSnapshot {
  quotes: ReadonlyMap<string, Snapshot>
  /** The latest read's outcome: 'error' when it failed, whatever came before it. */
  status: SnapshotStatus
  /** Read every quote of the list again. */
  retry: () => void
  /** Read these instruments again if their quotes are older than `maxAgeMs`. */
  resync: (keys: ReadonlySet<string>, maxAgeMs: number) => void
}

/**
 * One REST read of the list's quotes (`/api/v1/multiquotes`), for what the
 * ticks do not give: a level before the first tick (and all day for an
 * instrument that does not trade), and the previous close.
 *
 * Read when an instrument first appears (the list opens, a symbol is added),
 * and again only on an event: the feed reconnecting, the phone coming back to
 * the page after more than 15 seconds, a market opening or closing (for a page
 * left open overnight, the open is when the previous close becomes
 * yesterday's), or the list's own re-sync of instruments that have gone quiet
 * (`resync`). Never on a timer of its own: the ticks keep the levels current.
 *
 * An instrument that leaves the list takes its quote with it, so it is read
 * afresh if it comes back. One read at a time; a request that arrives during
 * one is folded into a single read after it.
 */
export function useQuoteSnapshot({
  apiKey,
  instruments,
  connectionEpoch,
  marketOpen,
}: {
  apiKey: string | null
  instruments: Instrument[]
  /** The market-data socket's sign-in count; a change is a reconnect. */
  connectionEpoch: number
  /**
   * Whether any of the main exchanges is open, or null while that is not known.
   * The same exchanges for every list, so switching lists never reads as a market
   * opening.
   */
  marketOpen: boolean | null
}): QuoteSnapshot {
  const [quotes, setQuotes] = useState<ReadonlyMap<string, Snapshot>>(() => new Map())
  const [status, setStatus] = useState<SnapshotStatus>('idle')
  const store = useRef(new Map<string, Snapshot>())
  const active = useRef(instruments)
  const busy = useRef(false)
  /** What to read once the read in flight is done: the youngest age asked for, per key or for all. */
  const again = useRef<{ maxAgeMs: number; keys: Set<string> | null } | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const read = useCallback(
    async function run(maxAgeMs: number, only: ReadonlySet<string> | null): Promise<void> {
      if (!apiKey) return
      if (busy.current) {
        const queued = again.current
        again.current = {
          maxAgeMs: queued ? Math.min(queued.maxAgeMs, maxAgeMs) : maxAgeMs,
          keys:
            queued === null
              ? only && new Set(only)
              : queued.keys === null || only === null
                ? null
                : new Set([...queued.keys, ...only]),
        }
        return
      }
      const at = Date.now()
      const due = active.current.filter(({ symbol, exchange }) => {
        const key = keyOf(symbol, exchange)
        if (only && !only.has(key)) return false
        const held = store.current.get(key)
        return !held || at - held.at > maxAgeMs
      })
      if (due.length === 0) return

      busy.current = true
      setStatus((previous) => (previous === 'ready' ? previous : 'loading'))
      try {
        const response = await tradingApi.getMultiQuotes(apiKey, due)
        if (!alive.current) return
        if (response.status === 'success' && Array.isArray(response.results)) {
          const wanted = new Set(active.current.map((each) => keyOf(each.symbol, each.exchange)))
          const next = new Map(store.current)
          for (const result of response.results) {
            const key = keyOf(result.symbol, result.exchange)
            // A symbol the broker could not quote comes back with an error and
            // no data; one that left the list while the read was out is dropped.
            if (result?.data && wanted.has(key)) next.set(key, { quote: result.data, at })
          }
          store.current = next
          setQuotes(next)
          setStatus('ready')
        } else {
          setStatus('error')
        }
      } catch {
        if (alive.current) setStatus('error')
      } finally {
        busy.current = false
        const queued = again.current
        again.current = null
        if (alive.current && queued !== null) void run(queued.maxAgeMs, queued.keys)
      }
    },
    [apiKey]
  )

  // The list opening, growing or changing. A key that left takes its quote with it.
  const instrumentsKey = instruments
    .map(({ symbol, exchange }) => keyOf(symbol, exchange))
    .sort()
    .join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: instrumentsKey is the identity of the list; the read itself takes the list from `active`
  useEffect(() => {
    active.current = instruments
    const wanted = new Set(instruments.map(({ symbol, exchange }) => keyOf(symbol, exchange)))
    if ([...store.current.keys()].some((key) => !wanted.has(key))) {
      store.current = new Map([...store.current].filter(([key]) => wanted.has(key)))
      setQuotes(store.current)
    }
    if (instruments.length === 0) {
      setStatus('idle')
      return
    }
    void read(SNAPSHOT_FRESH_MS, null)
  }, [instrumentsKey, read])

  // The feed reconnecting.
  const lastEpoch = useRef(connectionEpoch)
  useEffect(() => {
    if (lastEpoch.current === connectionEpoch) return
    lastEpoch.current = connectionEpoch
    void read(RECONNECT_REREAD_MS, null)
  }, [connectionEpoch, read])

  // A market opening or closing. Not the calendar arriving: unknown to known is no change.
  const lastOpen = useRef(marketOpen)
  useEffect(() => {
    const before = lastOpen.current
    lastOpen.current = marketOpen
    if (before !== null && marketOpen !== null && before !== marketOpen) void read(0, null)
  }, [marketOpen, read])

  // Back on the page after a while away.
  useEffect(() => {
    let hiddenAt: number | null = document.visibilityState === 'hidden' ? Date.now() : null
    const changed = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now()
        return
      }
      if (hiddenAt !== null && Date.now() - hiddenAt > AWAY_MS) void read(0, null)
      hiddenAt = null
    }
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [read])

  const retry = useCallback(() => void read(0, null), [read])
  const resync = useCallback(
    (keys: ReadonlySet<string>, maxAgeMs: number) => void read(maxAgeMs, keys),
    [read]
  )
  return { quotes, status, retry, resync }
}
