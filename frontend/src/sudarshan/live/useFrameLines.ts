/**
 * Live market data for a set of instruments, drawn once per animation frame.
 *
 * `useMarketData` keeps its map in React state and copies it on every tick,
 * so each tick re-renders the component that holds it. A watchlist of 250
 * symbols takes many ticks a second, and redrawing for every one of them is
 * work the phone does not need: nothing on a screen moves faster than its
 * frames. This hook subscribes through the same shared `MarketDataManager`
 * (the tab's one socket, ref-counted), writes each tick into a ref, and
 * re-renders once per frame with the newest line for every instrument. A tick
 * is on screen within one frame, about 16 ms, and SUDARSHAN adds no other
 * delay to it.
 *
 * Subscriptions follow the set by difference: an instrument that stays is not
 * unsubscribed and subscribed again when another joins or leaves, so its line
 * and its subscription survive. One that leaves takes its line and its tick
 * history with it, and starts empty if it comes back.
 *
 * The direction of each instrument's last tick is kept here too, tick by tick,
 * so several ticks inside one frame still show the last one's direction. Only
 * a WebSocket tick has a direction. A REST fallback update clears it, as does
 * a reconnect: the first frames after one are the broker's current state, not
 * a move.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMarketDataContextOptional } from '@/contexts/MarketDataContext'
import { MarketDataManager, type SubscriptionMode, type SymbolData } from '@/lib/MarketDataManager'
import { keyOf } from '@/sudarshan/dashboard/live'
import { type TickDirection, tickDirection } from '@/sudarshan/watchlist/rows'

export interface Instrument {
  symbol: string
  exchange: string
}

/** The socket as the screen needs to know it. */
export interface FeedState {
  /** Signed in and not paused: ticks can arrive. */
  connected: boolean
  /** Upstream's REST fallback is polling in its place. */
  fallback: boolean
  /** Counts sign-ins; a change is a reconnect. */
  epoch: number
}

export interface FrameLines {
  /** The newest cache line of each instrument in the set that has one. */
  lines: ReadonlyMap<string, SymbolData>
  /** The direction of each instrument's last tick, once it has moved. */
  directions: ReadonlyMap<string, TickDirection>
  feed: FeedState
}

type Frame = (callback: () => void) => () => void

/** The browser's next frame, or a 16 ms timer where there is none. */
const nextFrame: Frame = (callback) => {
  if (typeof requestAnimationFrame === 'function') {
    const id = requestAnimationFrame(() => callback())
    return () => cancelAnimationFrame(id)
  }
  const id = setTimeout(callback, 16)
  return () => clearTimeout(id)
}

/** The shared manager, as `useMarketData` finds it. */
export function useMarketDataManager(): MarketDataManager {
  const context = useMarketDataContextOptional()
  return useRef(context?.manager ?? MarketDataManager.getInstance()).current
}

function feedOf(state: ReturnType<MarketDataManager['getState']>): FeedState {
  return {
    connected: state.isAuthenticated && !state.isPaused,
    fallback: state.isFallbackMode,
    epoch: state.connectionEpoch,
  }
}

interface History {
  ltp: number
  /**
   * The update that brought it. The manager makes a new one per message, so
   * the same one seen twice (on subscribing, and from the cache) is one tick.
   */
  line: SymbolData
  direction: TickDirection | null
}

export function useFrameLines({
  instruments,
  mode,
  enabled = true,
}: {
  instruments: Instrument[]
  mode: SubscriptionMode
  enabled?: boolean
}): FrameLines {
  const manager = useMarketDataManager()
  const [feed, setFeed] = useState<FeedState>(() => feedOf(manager.getState()))
  const [drawn, setDrawn] = useState<{
    lines: ReadonlyMap<string, SymbolData>
    directions: ReadonlyMap<string, TickDirection>
  }>(() => ({ lines: new Map(), directions: new Map() }))

  const wanted = useRef(new Set<string>())
  const subscriptions = useRef(new Map<string, () => void>())
  const subscribedMode = useRef(mode)
  const pending = useRef(new Map<string, SymbolData>())
  const history = useRef(new Map<string, History>())
  const cancelFrame = useRef<(() => void) | null>(null)

  /** Draw what the ticks since the last frame left, once. */
  const draw = useCallback(() => {
    cancelFrame.current = null
    const batch = pending.current
    pending.current = new Map()
    const keys = [...wanted.current]
    const directions = new Map<string, TickDirection>()
    for (const key of keys) {
      const direction = history.current.get(key)?.direction
      if (direction) directions.set(key, direction)
    }
    setDrawn((previous) => {
      const lines = new Map<string, SymbolData>()
      for (const key of keys) {
        const line = batch.get(key) ?? previous.lines.get(key)
        if (line) lines.set(key, line)
      }
      return { lines, directions }
    })
  }, [])

  const schedule = useCallback(() => {
    if (cancelFrame.current === null) cancelFrame.current = nextFrame(draw)
  }, [draw])

  /** One update from the manager: kept for the next frame, its direction noted now. */
  const receive = useCallback(
    (line: SymbolData) => {
      const key = keyOf(line.symbol, line.exchange)
      if (!wanted.current.has(key) || line.lastUpdate === undefined) return
      const ltp = line.data.ltp
      const held = history.current.get(key)
      if (typeof ltp === 'number' && Number.isFinite(ltp) && ltp > 0 && held?.line !== line) {
        history.current.set(key, {
          ltp,
          line,
          direction:
            line.updateSource === 'websocket'
              ? tickDirection(held?.ltp ?? null, ltp, held?.direction ?? null)
              : null,
        })
      }
      pending.current.set(key, line)
      schedule()
    },
    [schedule]
  )

  // The socket's state, kept only when something the screen reads changed.
  useEffect(
    () =>
      manager.addStateListener((state) => {
        const next = feedOf(state)
        setFeed((previous) =>
          previous.connected === next.connected &&
          previous.fallback === next.fallback &&
          previous.epoch === next.epoch
            ? previous
            : next
        )
      }),
    [manager]
  )

  // A reconnect: the first frames after it are the broker's state, not a move.
  const lastEpoch = useRef(feed.epoch)
  useEffect(() => {
    if (lastEpoch.current === feed.epoch) return
    lastEpoch.current = feed.epoch
    for (const [key, held] of history.current)
      history.current.set(key, { ...held, direction: null })
    schedule()
  }, [feed.epoch, schedule])

  const instrumentsKey = useMemo(
    () =>
      enabled
        ? instruments
            .map(({ symbol, exchange }) => keyOf(symbol, exchange))
            .sort()
            .join(',')
        : '',
    [instruments, enabled]
  )

  // Subscribe the instruments that joined, release the ones that left.
  // biome-ignore lint/correctness/useExhaustiveDependencies: instrumentsKey is the identity of the set; the array itself is new on every render of a caller
  useEffect(() => {
    if (subscribedMode.current !== mode) {
      for (const unsubscribe of subscriptions.current.values()) unsubscribe()
      subscriptions.current.clear()
      subscribedMode.current = mode
    }
    const next = new Map<string, Instrument>()
    if (enabled) {
      for (const instrument of instruments) {
        next.set(keyOf(instrument.symbol, instrument.exchange), instrument)
      }
    }
    wanted.current = new Set(next.keys())

    for (const [key, unsubscribe] of subscriptions.current) {
      if (next.has(key)) continue
      unsubscribe()
      subscriptions.current.delete(key)
      history.current.delete(key)
      pending.current.delete(key)
    }

    if (next.size > 0) {
      const state = manager.getState()
      if (!state.isConnected && !state.isPaused) void manager.connect()
    }

    for (const [key, { symbol, exchange }] of next) {
      if (subscriptions.current.has(key)) continue
      subscriptions.current.set(key, manager.subscribe(symbol, exchange, mode, receive))
      const cached = manager.getCachedData(symbol, exchange)
      if (cached) receive(cached)
    }
    schedule()
  }, [instrumentsKey, mode, manager, receive, schedule])

  // Leaving: every subscription and the pending frame go.
  useEffect(
    () => () => {
      for (const unsubscribe of subscriptions.current.values()) unsubscribe()
      subscriptions.current.clear()
      history.current.clear()
      pending.current.clear()
      cancelFrame.current?.()
      cancelFrame.current = null
    },
    []
  )

  return { lines: drawn.lines, directions: drawn.directions, feed }
}
