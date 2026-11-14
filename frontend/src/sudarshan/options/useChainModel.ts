/**
 * One expiry's chain, real-time (M11.1, O4): the snapshot, the ticks of the followed window, and
 * the model the four tabs read.
 *
 * Ticks. The window's legs and the nearest future stream in Quote mode and an index's spot in LTP
 * mode, over the tab's one shared socket (`useFrameLines`, drawn once a frame, subscribed by
 * difference). Never more than `FOLLOW_CAP` instruments (`window.ts`).
 *
 * The snapshot is read once per event, never as a way to move prices: when the chain opens or
 * changes; when the socket signs in again; when the page returns after 15 s away; when the market
 * opens or closes; when the window re-centres; on Refresh; and quietly every 60 s while the page is
 * in view and the market is open (it brings the far strikes' open interest and yesterday's). Reads
 * are at least 5 s apart for one chain. When the first read fails, upstream's own chain is read
 * once in its place (no previous open interest, so OI change then reads "not available").
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SymbolData } from '@/lib/MarketDataManager'
import { type FeedLine, keyOf, priceOf } from '@/sudarshan/dashboard/live'
import { type FeedState, useFrameLines } from '@/sudarshan/live/useFrameLines'
import { instrumentOf } from '@/sudarshan/watchlist/follow'
import { statusLine, type TickDirection } from '@/sudarshan/watchlist/rows'
import { type Listing, type ReadState, readChain, readFallback } from './api'
import { type ChainModel, type ChainSnapshot, chainModel } from './model'
import { atmStrike, type FollowPlan, followPlan, nextCentre, type StrikeKeys } from './window'

/** The quiet re-sync while the page is in view and the market is open. */
export const RESYNC_MS = 60_000
/** Reads of one chain are at least this far apart (the server keeps an answer this long). */
export const MIN_GAP_MS = 5_000
/** Away longer than this, the snapshot is read again on return. */
export const AWAY_MS = 15_000

export interface SnapshotStatus {
  /** `reading` before the first answer of a chain. */
  state: ReadState | 'reading'
  /** The server's sentence when the last read was not `ok`. */
  words: string | null
  /** Where the snapshot held came from; null when none is held. */
  source: 'upstox' | 'fallback' | null
}

export interface LiveChain {
  model: ChainModel | null
  status: SnapshotStatus
  /** What the line over the window says. */
  line: FeedLine
  feed: FeedState
  spot: { ltp: number | null; change: number | null; changePct: number | null }
  future: { ltp: number | null; basis: number | null } | null
  plan: FollowPlan
  lines: ReadonlyMap<string, SymbolData>
  directions: ReadonlyMap<string, TickDirection>
  refresh: () => void
}

const NO_LINES: ReadonlyMap<string, SymbolData> = new Map()
const closeOf = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null

export function useChainModel({
  listing,
  expiry,
  apiKey,
  marketOpen,
  now,
  sheet,
  enabled = true,
}: {
  listing: Listing | null
  /** ISO; null until one is chosen. */
  expiry: string | null
  apiKey: string | null
  marketOpen: boolean | null
  now: number
  /** The open sheet's instruments (EXCHANGE:SYMBOL), followed while it is open. */
  sheet?: readonly string[]
  enabled?: boolean
}): LiveChain {
  const chainKey = listing && expiry ? `${listing.exchange}:${listing.underlying}:${expiry}` : null
  const stopsAt = listing?.expiries.find((each) => each.date === expiry)?.stops_at ?? null
  const [held, setHeld] = useState<{
    key: string
    snapshot: ChainSnapshot | null
    status: SnapshotStatus
  } | null>(null)
  const mine = held !== null && held.key === chainKey ? held : null
  const snapshot = mine?.snapshot ?? null
  const [centre, setCentre] = useState<{ key: string; strike: number; at: number } | null>(null)

  // -- what the socket follows -------------------------------------------------------------
  const spotKey = listing ? keyOf(listing.spot.symbol, listing.spot.exchange) : null
  const futureKey = listing?.future ? keyOf(listing.future.symbol, listing.future.exchange) : null
  const spotMode = listing?.spot.exchange.endsWith('_INDEX') ? 'LTP' : 'Quote'
  const ladder = useMemo<StrikeKeys[]>(
    () =>
      (snapshot?.strikes ?? []).map((each) => ({
        strike: each.strike,
        ce: each.ce ? keyOf(each.ce.symbol, each.ce.exchange) : undefined,
        pe: each.pe ? keyOf(each.pe.symbol, each.pe.exchange) : undefined,
      })),
    [snapshot]
  )
  const sheetKey = sheet?.join(',') ?? ''
  // Until the effect below has set a centre, the money itself is the centre: the first window
  // is the right one, and nothing is subscribed only to be let go a moment later.
  const strikes = useMemo(() => ladder.map((each) => each.strike), [ladder])
  const firstAtm = useMemo(() => atmStrike(snapshot?.spot ?? null, strikes), [snapshot, strikes])
  const centreStrike = centre !== null && centre.key === chainKey ? centre.strike : firstAtm
  const plan = useMemo(
    () =>
      followPlan({
        ladder,
        centre: centreStrike,
        spot: spotKey,
        spotMode,
        future: futureKey,
        sheet: sheetKey === '' ? [] : sheetKey.split(','),
      }),
    [ladder, centreStrike, spotKey, spotMode, futureKey, sheetKey]
  )
  const quoteSet = useMemo(() => plan.quote.map(instrumentOf), [plan.quote])
  const ltpSet = useMemo(() => plan.ltp.map(instrumentOf), [plan.ltp])
  const quote = useFrameLines({ instruments: quoteSet, mode: 'Quote', enabled })
  const last = useFrameLines({ instruments: ltpSet, mode: 'LTP', enabled })
  const feed = quote.feed

  const spotLine = spotKey ? (last.lines.get(spotKey) ?? quote.lines.get(spotKey)) : undefined
  const spotLtp = priceOf(spotLine)?.ltp ?? null
  const futureLtp = futureKey ? (priceOf(quote.lines.get(futureKey))?.ltp ?? null) : null

  // -- the window's centre -----------------------------------------------------------------
  const atm = atmStrike(spotLtp ?? snapshot?.spot ?? null, strikes)
  useEffect(() => {
    if (chainKey === null) return
    const at = Date.now()
    setCentre((previous) => {
      const before = previous !== null && previous.key === chainKey ? previous : null
      const next = nextCentre({
        strikes,
        centre: before?.strike ?? null,
        atm,
        now: at,
        movedAt: before?.at ?? null,
      })
      if (next === null) return before === null ? previous : null
      if (before !== null && before.strike === next) return previous
      return { key: chainKey, strike: next, at }
    })
  }, [chainKey, strikes, atm])

  // -- the snapshot ------------------------------------------------------------------------
  const reading = useRef<{ key: string; stop: AbortController } | null>(null)
  const lastRead = useRef<{ key: string; at: number } | null>(null)
  const fellBack = useRef<string | null>(null)
  const holding = useRef(false)
  holding.current = snapshot !== null
  const facts = useRef({ listing, expiry, apiKey, stopsAt })
  facts.current = { listing, expiry, apiKey, stopsAt }
  const read = useCallback(
    (force = false) => {
      const { listing: of, expiry: day, apiKey: key, stopsAt: stops } = facts.current
      if (!enabled || !of || !day || chainKey === null) return
      if (reading.current?.key === chainKey) return
      const before = lastRead.current
      if (!force && before?.key === chainKey && Date.now() - before.at < MIN_GAP_MS) return
      reading.current?.stop.abort()
      const stop = new AbortController()
      reading.current = { key: chainKey, stop }
      lastRead.current = { key: chainKey, at: Date.now() }
      setHeld((previous) =>
        previous?.key === chainKey
          ? previous
          : {
              key: chainKey,
              snapshot: null,
              status: { state: 'reading', words: null, source: null },
            }
      )
      ;(async () => {
        const answer = await readChain(of.exchange, of.underlying, day, stop.signal)
        let fallback: ChainSnapshot | null = null
        const failed = answer.state === 'unavailable' || answer.state === 'wait'
        if (failed && !holding.current && key && stops !== null && fellBack.current !== chainKey) {
          fellBack.current = chainKey // once for a chain, and only while nothing is held
          fallback = await readFallback(key, of, day, stops, Date.now())
        }
        if (stop.signal.aborted) return
        setHeld((previous) => {
          const kept = previous?.key === chainKey ? previous : null
          if (answer.state === 'ok' && answer.chain)
            return {
              key: chainKey,
              snapshot: answer.chain,
              status: { state: 'ok', words: null, source: 'upstox' },
            }
          const snapshot = kept?.snapshot ?? fallback
          const source = kept?.snapshot ? kept.status.source : fallback ? 'fallback' : null
          return {
            key: chainKey,
            snapshot: answer.state === 'signin' || answer.state === 'none' ? null : snapshot,
            status: { state: answer.state, words: answer.words, source },
          }
        })
      })()
        .catch(() => {})
        .finally(() => {
          if (reading.current?.stop === stop) reading.current = null
        })
    },
    [chainKey, enabled]
  )

  // Open, or another chain; leaving stops the read in flight.
  useEffect(() => {
    read()
    return () => {
      reading.current?.stop.abort()
      reading.current = null
    }
  }, [read])

  // The socket signed in again, the market opened or closed, the window moved.
  const seen = useRef({ epoch: feed.epoch, marketOpen, centre: centreStrike, key: chainKey })
  useEffect(() => {
    const was = seen.current
    seen.current = { epoch: feed.epoch, marketOpen, centre: centreStrike, key: chainKey }
    if (was.key !== chainKey) return
    const moved = was.centre !== null && centreStrike !== null && was.centre !== centreStrike
    const turned = was.marketOpen !== null && marketOpen !== null && was.marketOpen !== marketOpen
    if (was.epoch !== feed.epoch || moved || turned) read()
  }, [feed.epoch, marketOpen, centreStrike, chainKey, read])

  // Back after 15 s away; and the quiet re-sync while in view and open. Nothing runs while hidden.
  useEffect(() => {
    let hiddenAt: number | null = document.visibilityState === 'hidden' ? Date.now() : null
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now()
        return
      }
      if (hiddenAt !== null && Date.now() - hiddenAt > AWAY_MS) read()
      hiddenAt = null
    }
    document.addEventListener('visibilitychange', onVisibility)
    const timer =
      marketOpen === true
        ? setInterval(() => {
            if (document.visibilityState === 'visible') read()
          }, RESYNC_MS)
        : undefined
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      clearInterval(timer)
    }
  }, [read, marketOpen])

  // -- the model ---------------------------------------------------------------------------
  const second = Math.floor(now / 1000)
  // biome-ignore lint/correctness/useExhaustiveDependencies: the Greeks follow the clock by the second, not by the millisecond
  const model = useMemo(
    () =>
      snapshot
        ? chainModel({
            snapshot,
            lines: enabled ? quote.lines : NO_LINES,
            followed: plan.strikes,
            spot: spotLtp,
            future: futureLtp,
            now,
          })
        : null,
    [snapshot, quote.lines, plan.strikes, spotLtp, futureLtp, second, enabled]
  )

  let lastTickAt: number | null = null
  let lastUpdateAt: number | null = null
  for (const each of [...quote.lines.values(), ...last.lines.values()]) {
    if (each.lastUpdate === undefined) continue
    if (lastUpdateAt === null || each.lastUpdate > lastUpdateAt) lastUpdateAt = each.lastUpdate
    if (each.updateSource === 'websocket' && (lastTickAt === null || each.lastUpdate > lastTickAt))
      lastTickAt = each.lastUpdate
  }
  const stopped = stopsAt !== null && now >= stopsAt
  const line: FeedLine = stopped
    ? { kind: 'closed', stoppedAt: stopsAt }
    : statusLine({
        now,
        marketOpen,
        lastTickAt,
        lastUpdateAt: lastUpdateAt ?? snapshot?.as_of ?? null,
        connected: feed.connected,
        fallback: feed.fallback,
      })

  const shownSpot = spotLtp ?? model?.spot ?? null
  const prevClose = closeOf(spotLine?.data.prev_close) ?? closeOf(spotLine?.data.close)
  const change = shownSpot !== null && prevClose !== null ? shownSpot - prevClose : null
  return {
    model,
    status: mine?.status ?? { state: 'reading', words: null, source: null },
    line,
    feed,
    spot: {
      ltp: shownSpot,
      change,
      changePct: change !== null && prevClose !== null ? (change / prevClose) * 100 : null,
    },
    future:
      futureKey === null
        ? null
        : {
            ltp: futureLtp,
            basis: futureLtp !== null && shownSpot !== null ? futureLtp - shownSpot : null,
          },
    plan,
    lines: quote.lines,
    directions: quote.directions,
    refresh: useCallback(() => read(true), [read]),
  }
}
