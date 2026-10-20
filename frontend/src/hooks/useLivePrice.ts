import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { type QuotesData, tradingApi } from '@/api/trading'
import { useMarketData } from '@/hooks/useMarketData'
import { useMarketStatus } from '@/hooks/useMarketStatus'
import { useAuthStore } from '@/stores/authStore'
// SUDARSHAN: how a row is priced (newest source, broker P&L moved by ticks), M6.2/M6.3.
import { isClosedRow, type LiveRowFields, priceRow, TickDirections } from '@/sudarshan/books/prices'
import { useSpacedRead } from '@/sudarshan/books/useSpacedRead'

/**
 * Base interface for items that can have live price data
 */
export interface PriceableItem {
  symbol: string
  exchange: string
  ltp?: number
  pnl?: number
  pnlpercent?: number
  quantity?: number
  average_price?: number
  today_realized_pnl?: number // Sandbox: today's realized P&L from closed partial trades
  lot_size?: number // Contract multiplier (e.g. 0.01 for Delta Exchange ETHUSD.P)
}

/**
 * A position's P&L at a given price: today's realized P&L from partial closes
 * (the sandbox reports it as `today_realized_pnl`) plus the unrealized P&L of
 * what is still held. A long gains as the price rises and a short as it falls.
 * Quantity is in units, times the contract multiplier where the broker has one
 * (`lot_size`, e.g. 0.01 for Delta Exchange ETHUSD.P; 1 everywhere else, since
 * Indian F&O quantities already count lots times the lot size).
 *
 * Null when the position cannot be priced: no price, or no average price. This
 * is the evaluator `useLivePrice` applies to every open position.
 *
 * SUDARSHAN: lifted out of the hook unchanged and exported, so the live
 * dashboard prices positions with this evaluator instead of a copy of it.
 */
export function positionPnlAt(
  item: PriceableItem,
  ltp: number | undefined
): { pnl: number; pnlPercent: number } | null {
  const qty = item.quantity || 0
  const avgPrice = item.average_price || 0
  if (!ltp || avgPrice <= 0) return null

  // Contract multiplier: e.g. 0.01 for Delta Exchange ETHUSD.P (1 lot = 0.01 ETH)
  // Defaults to 1 for all standard brokers where qty is already in underlying units
  const lotSize = item.lot_size ?? 1

  // Calculate unrealized P&L based on position direction
  // Long (qty > 0): profit when ltp > avgPrice
  // Short (qty < 0): profit when ltp < avgPrice
  const unrealizedPnl =
    qty > 0 ? (ltp - avgPrice) * qty * lotSize : (avgPrice - ltp) * Math.abs(qty) * lotSize

  // Total P&L = today's realized (from partial closes) + current unrealized
  const pnl = (item.today_realized_pnl || 0) + unrealizedPnl

  // P&L% based on total P&L and investment
  const investment = Math.abs(avgPrice * qty)
  return { pnl, pnlPercent: investment > 0 ? (pnl / investment) * 100 : 0 }
}

/**
 * Configuration options for useLivePrice hook
 */
export interface UseLivePriceOptions {
  /** Whether the hook is enabled (default: true) */
  enabled?: boolean
  /**
   * Time in ms after which WebSocket data is considered stale (default: 5000)
   *
   * SUDARSHAN: kept for callers and no longer used. A price is chosen by how
   * recently it was confirmed (src/sudarshan/books/prices.ts), and how old the
   * figures on screen are is said by the page's status line.
   */
  staleThreshold?: number
  /**
   * Whether to read REST quotes (default: true)
   *
   * SUDARSHAN: the first snapshot of each symbol set, read again when the
   * market-data socket signs in again and on coming back to the page after
   * more than 15 seconds. Never on a timer: ticks move the prices, and the
   * market-data manager's own fallback covers an outage.
   */
  useMultiQuotesFallback?: boolean
  /**
   * Interval in ms to refresh MultiQuotes data (default: 30000)
   *
   * SUDARSHAN: kept for callers and ignored. Timed polling of prices is not
   * done while the socket is healthy (rule 7).
   */
  multiQuotesRefreshInterval?: number
  /** Pause WebSocket and polling when tab is hidden (default: true) */
  pauseWhenHidden?: boolean
  /** Time in ms to wait before pausing when hidden (default: 5000) */
  pauseDelay?: number
  /**
   * SUDARSHAN: when the items' own figures (ltp, pnl) were asked of the
   * broker. Only prices newer than this move them. Left out, a row's figures
   * count from when this hook first saw them.
   */
  snapshotAt?: number | null
}

/** SUDARSHAN: where the prices on screen stand, for the page's status line. */
export interface LiveFeed {
  /** The newest WebSocket tick for any row that can still move, or null. */
  lastTickAt: number | null
  /** The newest confirmation of any figure shown: a tick, a REST quote or the book. */
  lastUpdateAt: number | null
  /** Rows whose prices are due to tick: open positions, holdings, watchlist rows. */
  due: number
  /** The market-data socket is up and signed in. */
  connected: boolean
}

/**
 * Return type for useLivePrice hook
 */
export interface UseLivePriceResult<T extends PriceableItem> {
  /** Enhanced items with real-time LTP and recalculated PnL */
  data: Array<T & LiveRowFields>
  /** Whether real-time data is available (WebSocket connected AND market open) */
  isLive: boolean
  /** Whether WebSocket is connected */
  isConnected: boolean
  /** Whether WebSocket is paused due to tab being hidden */
  isPaused: boolean
  /** Whether using REST API fallback instead of WebSocket */
  isFallbackMode: boolean
  /** Whether any market is currently open */
  isAnyMarketOpen: boolean
  /** Map of MultiQuotes data for external access if needed */
  multiQuotes: Map<string, QuotesData>
  /** Manually refresh MultiQuotes data */
  refreshMultiQuotes: () => Promise<void>
  /** SUDARSHAN: where the prices stand, for the status line. */
  feed: LiveFeed
  /** SUDARSHAN: the market calendar this hook reads, so a page need not read it again. */
  calendar: Pick<
    ReturnType<typeof useMarketStatus>,
    'isLoading' | 'error' | 'isMarketOpen' | 'isAnyMarketOpen'
  >
}

/** SUDARSHAN: coming back after more than this long away reads the quotes again. */
const QUOTES_AWAY_MS = 15_000

/**
 * SUDARSHAN: the quotes are read at most once in this long: a book gaining a
 * row with each fill, a reconnect and a return all ask, and within the gap
 * they make one read at its end.
 */
const QUOTES_GAP_MS = 5_000

/**
 * Centralized hook for real-time price data with automatic fallback.
 *
 * Priority chain:
 * 1. WebSocket LTP (when market is open and data is fresh)
 * 2. MultiQuotes API (fallback when WebSocket unavailable)
 * 3. REST API data (baseline from initial fetch)
 *
 * SUDARSHAN: the newest of the three wins instead (src/sudarshan/books/prices.ts),
 * an open row's P&L is the broker's own figure moved by the price change since
 * the book was read, and REST quotes are read only for the first snapshot and
 * re-syncs, never on a timer.
 *
 * @example
 * ```tsx
 * const { data: enhancedHoldings, isLive } = useLivePrice(holdings, {
 *   enabled: holdings.length > 0,
 *   useMultiQuotesFallback: true,
 * });
 * ```
 */
export function useLivePrice<T extends PriceableItem>(
  items: T[],
  options: UseLivePriceOptions = {}
): UseLivePriceResult<T> {
  const { enabled = true, useMultiQuotesFallback = true, snapshotAt } = options

  const { apiKey } = useAuthStore()
  const calendar = useMarketStatus()
  const { isAnyMarketOpen } = calendar
  const anyMarketOpen = isAnyMarketOpen()

  // State for MultiQuotes fallback data
  const [multiQuotes, setMultiQuotes] = useState<Map<string, QuotesData>>(new Map())
  // SUDARSHAN: when that snapshot was asked for, so a newer tick can beat it.
  const [quotesAt, setQuotesAt] = useState<number | null>(null)

  // Extract symbols for WebSocket subscription
  const symbols = useMemo(
    () =>
      items.map((item) => ({
        symbol: item.symbol,
        exchange: item.exchange,
      })),
    [items]
  )
  // SUDARSHAN: the symbol set by content, so a re-read book with the same
  // instruments does not read the quotes again.
  const symbolsKey = useMemo(
    () =>
      Array.from(new Set(symbols.map((s) => `${s.exchange}:${s.symbol}`)))
        .sort()
        .join(','),
    [symbols]
  )
  const symbolsRef = useRef(symbols)
  symbolsRef.current = symbols

  // WebSocket market data - connect when enabled, with visibility awareness
  const {
    data: marketData,
    isConnected: wsConnected,
    isAuthenticated: wsAuthenticated,
    isPaused: wsPaused,
    isFallbackMode,
    connectionEpoch,
  } = useMarketData({
    symbols,
    mode: 'LTP',
    enabled: enabled && items.length > 0,
  })

  // Effective live status
  const isLive = wsConnected && anyMarketOpen && !wsPaused

  /**
   * Fetch MultiQuotes data from API
   */
  // SUDARSHAN: an answer a newer read overtook is dropped, and the snapshot is
  // stamped with when it was asked for.
  const quoteRead = useRef(0)
  const fetchMultiQuotes = useCallback(async () => {
    const symbolsList = symbolsRef.current
    if (!apiKey || symbolsList.length === 0 || !useMultiQuotesFallback) return

    const read = ++quoteRead.current
    const at = Date.now()
    try {
      const response = await tradingApi.getMultiQuotes(apiKey, symbolsList)
      if (read !== quoteRead.current) return

      if (response.status === 'success' && response.results) {
        const quotesMap = new Map<string, QuotesData>()
        response.results.forEach((result) => {
          const key = `${result.exchange}:${result.symbol}`
          if (result.data) {
            quotesMap.set(key, result.data)
          }
        })
        setMultiQuotes(quotesMap)
        setQuotesAt(at)
      }
    } catch {
      // Silently fail - MultiQuotes is a fallback mechanism
    }
  }, [apiKey, useMultiQuotesFallback])

  // SUDARSHAN: every reason to read the quotes goes through one gate: at most
  // one read in QUOTES_GAP_MS, none while the page is hidden, and one on
  // coming back after more than QUOTES_AWAY_MS away (or if anything asked
  // while it was hidden).
  const requestQuotes = useSpacedRead(() => void fetchMultiQuotes(), QUOTES_GAP_MS, {
    awayMs: enabled && useMultiQuotesFallback ? QUOTES_AWAY_MS : null,
  })

  // SUDARSHAN: the first snapshot of each symbol set. No interval: ticks move
  // the prices from here, and the manager's fallback covers a feed outage.
  useEffect(() => {
    if (!enabled || !symbolsKey || !useMultiQuotesFallback) return
    requestQuotes()
  }, [enabled, symbolsKey, useMultiQuotesFallback, requestQuotes])

  // SUDARSHAN: read again when the market-data socket signs in again (a
  // reconnect, or the phone waking), for instruments that do not tick soon.
  const epochSeen = useRef<number | null>(null)
  useEffect(() => {
    if (!wsAuthenticated) return
    if (epochSeen.current === null || connectionEpoch === epochSeen.current) {
      epochSeen.current = connectionEpoch
      return
    }
    epochSeen.current = connectionEpoch
    if (!enabled || !useMultiQuotesFallback) return
    requestQuotes()
  }, [wsAuthenticated, connectionEpoch, enabled, useMultiQuotesFallback, requestQuotes])

  /**
   * Enhance items with real-time LTP and recalculated P&L
   *
   * SUDARSHAN: each row from the newest of its tick, the quote snapshot and
   * its own book figures; an open row's P&L is the broker's moved by the
   * price change since the book was read; a closed row keeps the broker's
   * figures (src/sudarshan/books/prices.ts). A row's book time is the
   * caller's `snapshotAt`, or when its figures were first seen here.
   */
  const bookStamps = useRef(new Map<string, { signature: string; at: number }>())
  const directions = useRef(new TickDirections())
  const enhancedData = useMemo(() => {
    const now = Date.now()
    const rowKeys = new Set<string>()
    const symbolKeys = new Set<string>()
    const priced = items.map((item) => {
      const key = `${item.exchange}:${item.symbol}`
      symbolKeys.add(key)
      let bookAt = snapshotAt ?? null
      if (snapshotAt === undefined || snapshotAt === null) {
        const rowKey = `${key}:${(item as { product?: string }).product ?? ''}`
        rowKeys.add(rowKey)
        const signature = `${item.quantity}|${item.average_price}|${item.ltp}|${item.pnl}`
        let stamp = bookStamps.current.get(rowKey)
        if (!stamp || stamp.signature !== signature) {
          stamp = { signature, at: now }
          bookStamps.current.set(rowKey, stamp)
        }
        bookAt = stamp.at
      }
      const tick = marketData.get(key)
      const quote = multiQuotes.get(key)
      const price = priceRow(
        item,
        {
          tick,
          quoteLtp: quote?.ltp,
          quoteAt: quotesAt,
          quoteTradeAt: (quote as { last_trade_time?: unknown } | undefined)?.last_trade_time,
          bookAt,
          // A tick from this session of the feed: none while it is down.
          epoch: wsAuthenticated ? connectionEpoch : null,
        },
        positionPnlAt
      )
      // The direction of the last tick, as a broker app shows it; a closed row
      // keeps the broker's frozen price and shows none.
      const tickLtp = tick?.data?.ltp
      const direction = isClosedRow(item)
        ? null
        : price.live && typeof tickLtp === 'number' && tickLtp > 0
          ? directions.current.observe(key, tickLtp, item.ltp ?? quote?.ltp)
          : directions.current.get(key)
      return {
        ...item,
        ltp: price.ltp,
        pnl: price.pnl,
        pnlpercent: price.pnlpercent,
        _dataSource: price.source,
        _at: price.at,
        _live: price.live,
        _tick: direction,
      } as T & LiveRowFields
    })
    // Forget rows and instruments no longer shown, so a long session does not grow these.
    for (const rowKey of bookStamps.current.keys()) {
      if (!rowKeys.has(rowKey)) bookStamps.current.delete(rowKey)
    }
    directions.current.keep(symbolKeys)
    return priced
  }, [items, marketData, multiQuotes, quotesAt, snapshotAt, wsAuthenticated, connectionEpoch])

  // SUDARSHAN: the newest tick and the newest confirmation, over the rows
  // whose prices can still move, for the page's status line.
  const feed = useMemo<LiveFeed>(() => {
    let lastTickAt: number | null = null
    let lastUpdateAt: number | null = null
    let due = 0
    for (const row of enhancedData) {
      if (isClosedRow(row)) continue
      due += 1
      const tick = marketData.get(`${row.exchange}:${row.symbol}`)
      if (
        tick?.updateSource === 'websocket' &&
        typeof tick.lastUpdate === 'number' &&
        (lastTickAt === null || tick.lastUpdate > lastTickAt)
      ) {
        lastTickAt = tick.lastUpdate
      }
      if (row._at !== null && (lastUpdateAt === null || row._at > lastUpdateAt)) {
        lastUpdateAt = row._at
      }
    }
    return { lastTickAt, lastUpdateAt, due, connected: wsAuthenticated && !wsPaused }
  }, [enhancedData, marketData, wsAuthenticated, wsPaused])

  return {
    data: enhancedData,
    isLive,
    isConnected: wsConnected,
    isPaused: wsPaused,
    isFallbackMode,
    isAnyMarketOpen: anyMarketOpen,
    multiQuotes,
    refreshMultiQuotes: fetchMultiQuotes,
    feed,
    calendar,
  }
}

/**
 * Calculate aggregated stats from items with live price data
 */
export function calculateLiveStats<T extends PriceableItem>(
  items: T[],
  originalStats?: {
    totalholdingvalue?: number
    totalinvvalue?: number
    totalprofitandloss?: number
    totalpnlpercentage?: number
  }
) {
  if (!originalStats) return null

  let totalPnl = 0
  let totalInvestment = 0
  let totalHoldingValue = 0

  items.forEach((item) => {
    totalPnl += item.pnl || 0
    const avgPrice = item.average_price || 0
    const qty = item.quantity || 0
    const ltp = item.ltp || avgPrice
    totalInvestment += avgPrice * qty
    totalHoldingValue += ltp * qty
  })

  const totalPnlPercent = totalInvestment > 0 ? (totalPnl / totalInvestment) * 100 : 0

  return {
    ...originalStats,
    totalholdingvalue: totalHoldingValue,
    totalinvvalue: totalInvestment,
    totalprofitandloss: totalPnl,
    totalpnlpercentage: totalPnlPercent,
  }
}
