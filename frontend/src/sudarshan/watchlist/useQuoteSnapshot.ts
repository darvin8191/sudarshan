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

/**
 * One REST read of the list's quotes (`/api/v1/multiquotes`), for what the
 * ticks do not give: a level before the first tick (and all day for an
 * instrument that does not trade), and the previous close.
 *
 * Read when an instrument first appears (the list opens, a symbol is added),
 * and again only on an event: the feed reconnecting, the phone coming back to
 * the page after more than 15 seconds, or a market opening or closing (for a
 * page left open overnight, the open is when the previous close becomes
 * yesterday's). Never on a timer: the ticks keep the levels current. One read
 * at a time; a request that arrives during one is folded into a single read
 * after it.
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
}): { quotes: ReadonlyMap<string, Snapshot>; status: SnapshotStatus; retry: () => void } {
  const [quotes, setQuotes] = useState<ReadonlyMap<string, Snapshot>>(() => new Map())
  const [status, setStatus] = useState<SnapshotStatus>('idle')
  const store = useRef(new Map<string, Snapshot>())
  const active = useRef(instruments)
  const busy = useRef(false)
  const again = useRef<number | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Declared before the reads below, so they always see the current list.
  useEffect(() => {
    active.current = instruments
  }, [instruments])

  const readOlderThan = useCallback(
    async function read(maxAgeMs: number): Promise<void> {
      if (!apiKey) return
      if (busy.current) {
        again.current = again.current === null ? maxAgeMs : Math.min(again.current, maxAgeMs)
        return
      }
      const at = Date.now()
      const due = active.current.filter(({ symbol, exchange }) => {
        const held = store.current.get(keyOf(symbol, exchange))
        return !held || at - held.at > maxAgeMs
      })
      if (due.length === 0) return

      busy.current = true
      setStatus((previous) => (previous === 'ready' ? previous : 'loading'))
      const failed = () => setStatus((previous) => (previous === 'ready' ? previous : 'error'))
      try {
        const response = await tradingApi.getMultiQuotes(apiKey, due)
        if (!alive.current) return
        if (response.status === 'success' && Array.isArray(response.results)) {
          const next = new Map(store.current)
          for (const result of response.results) {
            // A symbol the broker could not quote comes back with an error and no data.
            if (result?.data)
              next.set(keyOf(result.symbol, result.exchange), { quote: result.data, at })
          }
          store.current = next
          setQuotes(next)
          setStatus('ready')
        } else {
          failed()
        }
      } catch {
        if (alive.current) failed()
      } finally {
        busy.current = false
        const pending = again.current
        again.current = null
        if (alive.current && pending !== null) void read(pending)
      }
    },
    [apiKey]
  )

  // The list opening, growing or changing.
  const instrumentsKey = instruments
    .map(({ symbol, exchange }) => keyOf(symbol, exchange))
    .sort()
    .join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: instrumentsKey is the identity of the list; the read itself takes the list from `active`
  useEffect(() => {
    if (instruments.length === 0) {
      setStatus((previous) => (previous === 'ready' ? previous : 'idle'))
      return
    }
    void readOlderThan(SNAPSHOT_FRESH_MS)
  }, [instrumentsKey, readOlderThan])

  // The feed reconnecting.
  const lastEpoch = useRef(connectionEpoch)
  useEffect(() => {
    if (lastEpoch.current === connectionEpoch) return
    lastEpoch.current = connectionEpoch
    void readOlderThan(RECONNECT_REREAD_MS)
  }, [connectionEpoch, readOlderThan])

  // A market opening or closing. Not the calendar arriving: unknown to known is no change.
  const lastOpen = useRef(marketOpen)
  useEffect(() => {
    const before = lastOpen.current
    lastOpen.current = marketOpen
    if (before !== null && marketOpen !== null && before !== marketOpen) void readOlderThan(0)
  }, [marketOpen, readOlderThan])

  // Back on the page after a while away.
  useEffect(() => {
    let hiddenAt: number | null = document.visibilityState === 'hidden' ? Date.now() : null
    const changed = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now()
        return
      }
      if (hiddenAt !== null && Date.now() - hiddenAt > AWAY_MS) void readOlderThan(0)
      hiddenAt = null
    }
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [readOlderThan])

  const retry = useCallback(() => void readOlderThan(0), [readOlderThan])
  return { quotes, status, retry }
}
