import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { type DepthData, type DepthLevel, type QuotesData, tradingApi } from '@/api/trading'
import { useMarketData } from '@/hooks/useMarketData'
import { useMarketStatus } from '@/hooks/useMarketStatus'
import { useAuthStore } from '@/stores/authStore'
// SUDARSHAN: optional tick fields, read defensively (src/sudarshan/books/prices.ts).
import { tickNumber, tickPrevClose } from '@/sudarshan/books/prices'
import { useOnReturn } from '@/sudarshan/books/useReturn'

/**
 * Depth data in normalized format (used by both WebSocket and REST)
 */
export interface NormalizedDepth {
  buy: DepthLevel[]
  sell: DepthLevel[]
}

/**
 * Combined quote and depth data
 */
/**
 * First usable price from the given sources, treating 0 as "not available".
 *
 * A real traded price is never 0, so a 0 from any source means that source has
 * nothing to say yet rather than that the instrument is worthless. Using ??
 * here would accept the 0 and stop, which is how a WebSocket tick with no
 * last-traded price ends up displayed as 0.00 while REST has the real number.
 */
function firstPrice(...values: (number | undefined | null)[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      return value
    }
  }
  return undefined
}

export interface LiveQuoteData {
  ltp?: number
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
  oi?: number
  change?: number
  changePercent?: number
  bidPrice?: number
  askPrice?: number
  bidSize?: number
  askSize?: number
  depth?: NormalizedDepth
}

/**
 * Configuration options for useLiveQuote hook
 */
export interface UseLiveQuoteOptions {
  /** Whether the hook is enabled (default: true) */
  enabled?: boolean
  /** WebSocket subscription mode: 'LTP', 'Quote', or 'Depth' (default: 'Depth') */
  mode?: 'LTP' | 'Quote' | 'Depth'
  /** Whether to fetch REST quotes as fallback (default: true) */
  useQuotesFallback?: boolean
  /** Whether to fetch REST depth as fallback (default: true) */
  useDepthFallback?: boolean
  /** Time in ms after which WebSocket data is considered stale (default: 5000) */
  staleThreshold?: number
  /**
   * Interval in ms to refresh REST data (default: 30000)
   *
   * SUDARSHAN: kept for callers and ignored. REST gives the first snapshot and
   * re-syncs (the market-data socket signing in again, coming back to the page
   * after more than 15 seconds); ticks do the rest (rule 7).
   */
  refreshInterval?: number
  /** Pause when tab is hidden (default: true) */
  pauseWhenHidden?: boolean
}

/**
 * Return type for useLiveQuote hook
 */
export interface UseLiveQuoteResult {
  /** Combined quote and depth data */
  data: LiveQuoteData
  /** Whether real-time data is available (WebSocket connected AND market open) */
  isLive: boolean
  /** Whether WebSocket is connected */
  isConnected: boolean
  /** Whether data is being loaded */
  isLoading: boolean
  /** Whether WebSocket is paused due to tab being hidden */
  isPaused: boolean
  /** Whether using REST API fallback instead of WebSocket */
  isFallbackMode: boolean
  /** Data source: 'websocket', 'rest', or 'none' */
  dataSource: 'websocket' | 'rest' | 'none'
  /** Manually refresh REST data */
  refresh: () => Promise<void>
  /** SUDARSHAN: where the figures stand, for a status line. */
  feed: {
    lastTickAt: number | null
    lastUpdateAt: number | null
    due: number
    connected: boolean
    /** No tick has come and the day's volume is 0: nothing has traded yet, so nothing is late. */
    noTrade: boolean
  }
  /** SUDARSHAN: the market calendar this hook reads. */
  calendar: Pick<
    ReturnType<typeof useMarketStatus>,
    'isLoading' | 'error' | 'isMarketOpen' | 'isAnyMarketOpen'
  >
}

/** SUDARSHAN: coming back after more than this long away reads the snapshot again. */
const AWAY_MS = 15_000

/** SUDARSHAN: two reasons to re-read this close together make one read. */
const MIN_GAP_MS = 3_000

/**
 * Centralized hook for real-time quote and depth data with automatic REST fallback.
 *
 * Similar to useLivePrice but for single symbol with full quote + depth support.
 * Use this for PlaceOrderDialog, symbol details, or anywhere you need live market data.
 *
 * Priority chain:
 * 1. WebSocket data (when market is open and data is fresh)
 * 2. REST API data (fallback when WebSocket unavailable)
 *
 * @example
 * ```tsx
 * const { data, isLive, isLoading } = useLiveQuote('RELIANCE', 'NSE', {
 *   enabled: dialogOpen,
 *   mode: 'Depth',
 * });
 *
 * // Access data
 * console.log(data.ltp, data.bidPrice, data.depth?.buy);
 * ```
 */
export function useLiveQuote(
  symbol: string,
  exchange: string,
  options: UseLiveQuoteOptions = {}
): UseLiveQuoteResult {
  const {
    enabled = true,
    mode = 'Depth',
    useQuotesFallback = true,
    useDepthFallback = true,
    staleThreshold = 5000,
  } = options

  const { apiKey } = useAuthStore()
  const calendar = useMarketStatus()
  const { isMarketOpen } = calendar

  // REST fallback state
  const [restQuotes, setRestQuotes] = useState<QuotesData | null>(null)
  const [restDepth, setRestDepth] = useState<DepthData | null>(null)
  const [isLoadingRest, setIsLoadingRest] = useState(false)
  // SUDARSHAN: when that snapshot was asked for, so a newer tick beats it field by field.
  const [restAt, setRestAt] = useState<number | null>(null)

  // Track last fetch time
  const lastFetchRef = useRef<number>(0)

  // Check if we have valid symbol/exchange
  const hasSymbol = !!symbol && !!exchange

  // WebSocket subscription
  const symbols = useMemo(
    () => (hasSymbol ? [{ symbol, exchange }] : []),
    [symbol, exchange, hasSymbol]
  )

  const {
    data: marketData,
    isConnected: wsConnected,
    isAuthenticated: wsAuthenticated,
    isPaused: wsPaused,
    isFallbackMode,
    connectionEpoch,
  } = useMarketData({
    symbols,
    mode,
    enabled: enabled && hasSymbol,
  })

  const wsEntry = marketData.get(`${exchange}:${symbol}`)
  const wsData = wsEntry?.data
  const wsLastUpdate = wsEntry?.lastUpdate
  // SUDARSHAN: a tick (or the manager's outage fallback) newer than the REST
  // snapshot wins; the snapshot fills only what the tick does not carry.
  const wsNewer = wsLastUpdate !== undefined && (restAt === null || wsLastUpdate >= restAt)

  // Check if market is open for this exchange
  const marketOpen = isMarketOpen(exchange)

  // Check if WebSocket data is fresh
  const hasWsData = !!(
    wsConnected &&
    wsData &&
    wsLastUpdate &&
    Date.now() - wsLastUpdate < staleThreshold
  )

  // Effective live status (WebSocket connected, data fresh, market open)
  const isLive = hasWsData && marketOpen && !wsPaused

  /**
   * Fetch REST data (quotes and/or depth)
   */
  // SUDARSHAN: an answer for an earlier symbol, or one a newer read overtook, is dropped.
  const restRead = useRef(0)
  const lastRestReadAt = useRef(0)
  const fetchRestData = useCallback(async () => {
    if (!apiKey || !hasSymbol) return

    const read = ++restRead.current
    const at = Date.now()
    lastRestReadAt.current = at
    const latest = () => read === restRead.current
    setIsLoadingRest(true)
    try {
      // Fetch quotes and depth in parallel
      const promises: Promise<void>[] = []

      if (useQuotesFallback) {
        promises.push(
          tradingApi
            .getQuotes(apiKey, symbol, exchange)
            .then((response) => {
              if (latest() && response.status === 'success' && response.data) {
                setRestQuotes(response.data)
              }
            })
            .catch(() => {})
        )
      }

      if (useDepthFallback && mode === 'Depth') {
        promises.push(
          tradingApi
            .getDepth(apiKey, symbol, exchange)
            .then((response) => {
              if (latest() && response.status === 'success' && response.data) {
                setRestDepth(response.data)
              }
            })
            .catch(() => {})
        )
      }

      await Promise.all(promises)
      lastFetchRef.current = Date.now()
      if (latest()) setRestAt(at)
    } finally {
      if (latest()) setIsLoadingRest(false)
    }
  }, [apiKey, symbol, exchange, hasSymbol, useQuotesFallback, useDepthFallback, mode])

  // Fetch on mount and when symbol changes
  // Use refs to track current state and avoid stale closures in intervals
  const enabledRef = useRef(enabled)
  const hasSymbolRef = useRef(hasSymbol)
  const fetchRestDataRef = useRef(fetchRestData)

  // Keep refs in sync with latest values
  useEffect(() => {
    enabledRef.current = enabled
    hasSymbolRef.current = hasSymbol
    fetchRestDataRef.current = fetchRestData
  }, [enabled, hasSymbol, fetchRestData])

  // biome-ignore lint/correctness/useExhaustiveDependencies: symbol/exchange intentionally restart the initial fetch when the watched instrument changes, even though the body reads them only via the stable fetchRestDataRef
  useEffect(() => {
    if (!enabled || !hasSymbol) return

    // Initial fetch - use ref to ensure we have the latest function
    // SUDARSHAN: the first snapshot only, no interval: ticks move the quote from here.
    fetchRestDataRef.current()
  }, [enabled, hasSymbol, symbol, exchange])

  // SUDARSHAN: read again when the market-data socket signs in again (a
  // reconnect, the phone waking), and on coming back after 15 s away.
  const epochSeen = useRef<number | null>(null)
  useEffect(() => {
    if (!wsAuthenticated) return
    if (epochSeen.current === null || connectionEpoch === epochSeen.current) {
      epochSeen.current = connectionEpoch
      return
    }
    epochSeen.current = connectionEpoch
    if (!enabledRef.current || !hasSymbolRef.current) return
    if (Date.now() - lastRestReadAt.current < MIN_GAP_MS) return
    fetchRestDataRef.current()
  }, [wsAuthenticated, connectionEpoch])
  useOnReturn(() => {
    if (!enabledRef.current || !hasSymbolRef.current) return
    if (Date.now() - lastRestReadAt.current < MIN_GAP_MS) return
    fetchRestDataRef.current()
  }, AWAY_MS)

  // Reset state when symbol changes
  // biome-ignore lint/correctness/useExhaustiveDependencies: symbol/exchange are the intended trigger to clear stale REST data when the watched instrument changes
  useEffect(() => {
    setRestQuotes(null)
    setRestDepth(null)
    // SUDARSHAN: and the snapshot time. A read still out for the old symbol was
    // overtaken by the new symbol's first read, so its answer is dropped.
    setRestAt(null)
  }, [symbol, exchange])

  // Convert REST depth format to normalized format
  const restDepthNormalized: NormalizedDepth | undefined = useMemo(() => {
    if (!restDepth) return undefined
    return {
      buy: restDepth.bids.map((level) => ({ price: level.price, quantity: level.quantity })),
      sell: restDepth.asks.map((level) => ({ price: level.price, quantity: level.quantity })),
    }
  }, [restDepth])

  // Merge WebSocket and REST data
  const mergedData: LiveQuoteData = useMemo(() => {
    // SUDARSHAN: the newer of the tick and the REST snapshot goes first, field
    // by field; the older fills only what the newer does not carry.
    const ws = wsNewer ? wsData : undefined
    const wsOld = wsNewer ? undefined : wsData

    // Determine best depth source
    const depth = ws?.depth ?? restDepthNormalized ?? wsOld?.depth

    // Determine best bid/ask from depth or quotes
    const bidPrice =
      ws?.depth?.buy?.[0]?.price ??
      ws?.bid_price ??
      restDepthNormalized?.buy?.[0]?.price ??
      restQuotes?.bid ??
      wsOld?.depth?.buy?.[0]?.price ??
      wsOld?.bid_price
    const askPrice =
      ws?.depth?.sell?.[0]?.price ??
      ws?.ask_price ??
      restDepthNormalized?.sell?.[0]?.price ??
      restQuotes?.ask ??
      wsOld?.depth?.sell?.[0]?.price ??
      wsOld?.ask_price
    const bidSize =
      ws?.depth?.buy?.[0]?.quantity ??
      ws?.bid_size ??
      restDepthNormalized?.buy?.[0]?.quantity ??
      wsOld?.depth?.buy?.[0]?.quantity ??
      wsOld?.bid_size
    const askSize =
      ws?.depth?.sell?.[0]?.quantity ??
      ws?.ask_size ??
      restDepthNormalized?.sell?.[0]?.quantity ??
      wsOld?.depth?.sell?.[0]?.quantity ??
      wsOld?.ask_size

    // Merge all data with priority: WebSocket > REST depth > REST quotes.
    //
    // Price fields use firstPrice, not ??. A feed can deliver a tick whose ltp
    // is 0 -- before the first trade of the session, or when the adapter has
    // depth but no last-traded price yet. ?? only falls through on null and
    // undefined, so a 0 would win and pin the display at 0.00 while depth shows
    // a real bid/ask, with the REST fallback never consulted. Treating 0 as
    // absent lets REST fill the gap.
    //
    // SUDARSHAN: the previous close is a tick's `prev_close` (or `cp`) when it
    // carries one, else the REST snapshot's. Never a tick's `close`: today's
    // Upstox adapter puts the running day bar there, and the completed feed
    // the same previous close, or 0 when unknown. Open interest: tick first.
    return {
      ltp: firstPrice(ws?.ltp, restDepth?.ltp, restQuotes?.ltp, wsOld?.ltp),
      open: firstPrice(ws?.open, restDepth?.open, restQuotes?.open, wsOld?.open),
      high: firstPrice(ws?.high, restDepth?.high, restQuotes?.high, wsOld?.high),
      low: firstPrice(ws?.low, restDepth?.low, restQuotes?.low, wsOld?.low),
      close: firstPrice(
        tickPrevClose(wsData) ?? undefined,
        restDepth?.prev_close,
        restQuotes?.prev_close
      ),
      volume: ws?.volume ?? restDepth?.volume ?? restQuotes?.volume ?? wsOld?.volume,
      oi: (ws ? tickNumber(ws, 'oi') : null) ?? restDepth?.oi ?? restQuotes?.oi,
      change: ws?.change,
      changePercent: ws?.change_percent,
      bidPrice,
      askPrice,
      bidSize,
      askSize,
      depth,
    }
  }, [wsData, wsNewer, restQuotes, restDepth, restDepthNormalized])

  // Calculate change if not available from WebSocket
  // SUDARSHAN: from the previous close whenever one is known, so the change
  // agrees with the close shown beside it (a tick's own change only when no
  // close is known); and a change of exactly 0 keeps its 0.00%.
  const dataWithChange: LiveQuoteData = useMemo(() => {
    const { ltp, close } = mergedData
    if (!ltp || !close) return mergedData

    const change = ltp - close
    const changePercent = (change / close) * 100

    return {
      ...mergedData,
      change,
      changePercent,
    }
  }, [mergedData])

  // Determine data source
  const dataSource: 'websocket' | 'rest' | 'none' = useMemo(() => {
    if (hasWsData) return 'websocket'
    if (restQuotes || restDepth) return 'rest'
    return 'none'
  }, [hasWsData, restQuotes, restDepth])

  // SUDARSHAN: where the quote stands, for the status line beside it.
  const lastTickAt =
    wsEntry?.updateSource === 'websocket' && typeof wsLastUpdate === 'number' ? wsLastUpdate : null
  const lastUpdateAt =
    typeof wsLastUpdate === 'number' && (restAt === null || wsLastUpdate > restAt)
      ? wsLastUpdate
      : restAt
  const feed = {
    lastTickAt,
    lastUpdateAt,
    due: enabled && hasSymbol ? 1 : 0,
    connected: wsAuthenticated && !wsPaused,
    noTrade: lastTickAt === null && dataWithChange.volume === 0,
  }

  return {
    data: dataWithChange,
    isLive,
    isConnected: wsConnected,
    isLoading: isLoadingRest && !restQuotes && !restDepth,
    isPaused: wsPaused,
    isFallbackMode,
    dataSource,
    refresh: fetchRestData,
    feed,
    calendar,
  }
}
