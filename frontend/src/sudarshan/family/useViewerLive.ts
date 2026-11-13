// SUDARSHAN P6 D4: ticks for a viewer's rows, one draw per frame. The same pieces as the Upstox
// tab's `useUpstoxLive` (`useFrameLines` for the ticks, `useBookFeed` for where the feed
// stands), over instruments the caller has already chosen, with the status line corrected by
// what the server's feed says (`withFeedState`). Nothing polls: the book is the first snapshot and
// ticks move it.
import { useMemo, useRef } from 'react'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import { useFrameLines } from '@/sudarshan/live/useFrameLines'
import { type Tick, tickOf } from '@/sudarshan/portfolio/liveTab'
import { useFamilyFeed } from './familyManager'
import { withFeedState } from './feedLine'
import { LIMITS } from './types'

export interface Instrument {
  symbol: string
  exchange: string
}

export interface ViewerLive {
  ticks: ReadonlyMap<string, Tick>
  directions: ReadonlyMap<string, 'up' | 'down'>
  line: ReturnType<typeof withFeedState>
  now: number
  /** True for a row whose price should be moving and is not. */
  stale: (instrument: Instrument | null) => boolean
  /** Instruments past the socket's cap: shown at the book's price, never "Real-time". */
  capped: ReadonlySet<string>
}

export function useViewerLive({
  instruments,
  marketOpen,
  cap = LIMITS.ws_symbols,
}: {
  instruments: readonly Instrument[]
  marketOpen: MarketOpen
  cap?: number
}): ViewerLive {
  const feedState = useFamilyFeed().state
  // Each instrument once, in screen order, the first `cap` of them.
  const { wanted, capped } = useMemo(() => {
    const seen = new Map<string, Instrument>()
    for (const instrument of instruments) {
      const key = keyOf(instrument.symbol, instrument.exchange)
      if (!seen.has(key)) seen.set(key, instrument)
    }
    const all = [...seen.entries()]
    return {
      wanted: all.slice(0, cap).map(([, instrument]) => instrument),
      capped: new Set(all.slice(cap).map(([key]) => key)),
    }
  }, [instruments, cap])

  const { lines, directions, feed } = useFrameLines({
    instruments: wanted,
    mode: 'LTP',
    enabled: wanted.length > 0,
  })

  // The newest good tick of each instrument: a price that is no price leaves the last one.
  const good = useRef(new Map<string, Tick>())
  const ticks = useMemo(() => {
    const keys = new Set(wanted.map((i) => keyOf(i.symbol, i.exchange)))
    for (const key of [...good.current.keys()]) if (!keys.has(key)) good.current.delete(key)
    for (const [key, line] of lines) {
      const tick = tickOf(line)
      if (tick) good.current.set(key, tick)
    }
    return new Map(good.current)
  }, [lines, wanted])

  const feedRows: FeedRow[] = useMemo(
    () =>
      wanted.map((instrument) => {
        const tick = ticks.get(keyOf(instrument.symbol, instrument.exchange))
        return {
          exchange: instrument.exchange,
          ltp: tick?.ltp,
          _at: tick?.at ?? null,
          _live: tick?.live === true,
        }
      }),
    [wanted, ticks]
  )
  const stamps = [...ticks.values()]
  const lastTickAt = stamps.reduce<number | null>(
    (best, tick) => (tick.live && (best === null || tick.at > best) ? tick.at : best),
    null
  )
  const lastUpdateAt = stamps.reduce<number | null>(
    (best, tick) => (best === null || tick.at > best ? tick.at : best),
    null
  )
  const book = useBookFeed(
    feedRows,
    { lastTickAt, lastUpdateAt, due: feedRows.length, connected: feed.connected },
    marketOpen
  )
  const stale = (instrument: Instrument | null): boolean => {
    if (!instrument) return false
    const key = keyOf(instrument.symbol, instrument.exchange)
    if (capped.has(key)) return false // labelled as not real-time instead
    const tick = ticks.get(key)
    return book.stale({
      exchange: instrument.exchange,
      ltp: tick?.ltp,
      _at: tick?.at ?? null,
      _live: tick?.live === true,
    })
  }
  return {
    ticks,
    directions: directions as ReadonlyMap<string, 'up' | 'down'>,
    line: withFeedState(book.line, feedState),
    now: book.now,
    stale,
    capped,
  }
}
