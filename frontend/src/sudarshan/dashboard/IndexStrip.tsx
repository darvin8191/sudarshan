import { Clock } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { type QuotesData, tradingApi } from '@/api/trading'
import { Skeleton } from '@/components/ui/skeleton'
import { useMarketData } from '@/hooks/useMarketData'
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatPrice } from './format'
import { useNow } from './hooks'
import { LiveLine } from './LiveLine'
import {
  feedLine,
  type IndexView,
  indexView,
  isStale,
  keyOf,
  latest,
  type MarketOpen,
  priceOf,
} from './live'
import { TREND_TEXT, TrendArrow } from './Trend'

/** The three the dashboard shows, in OpenAlgo's symbols for them. */
export const INDICES = [
  { symbol: 'NIFTY', exchange: 'NSE_INDEX', label: 'NIFTY 50' },
  { symbol: 'BANKNIFTY', exchange: 'NSE_INDEX', label: 'NIFTY BANK' },
  { symbol: 'INDIAVIX', exchange: 'NSE_INDEX', label: 'INDIA VIX' },
] as const

/** The instruments as the feed and the quote call take them: one array, so the subscription never churns. */
const INDEX_SYMBOLS = INDICES.map(({ symbol, exchange }) => ({ symbol, exchange }))

/** A read older than this is made again when the page comes back into view or the feed reconnects. */
export const SNAPSHOT_MAX_AGE_MS = 10 * 60_000

type Status = 'loading' | 'ready' | 'error'

interface Snapshot {
  quotes: Map<string, QuotesData>
  /** When the read that gave these quotes was sent. */
  at: number | null
  status: Status
}

/**
 * One REST read of the three quotes, for what the ticks do not give: a level
 * before the first tick (and all day when the market is shut and nothing
 * ticks), and the previous close, which the feed's ticks do not carry. Read on
 * arrival; again when NSE opens or closes, since at the open the previous close
 * becomes yesterday's; and when the page comes back into view or the feed
 * reconnects, if the last good read is over ten minutes old. Never on a timer:
 * the ticks keep the levels current.
 */
export function useIndexSnapshot(
  apiKey: string | null,
  nseOpen: boolean | null,
  connectionEpoch: number
): Snapshot {
  const [snapshot, setSnapshot] = useState<Snapshot>(() => ({
    quotes: new Map(),
    at: null,
    status: 'loading',
  }))
  const alive = useRef(true)
  const busy = useRef(false)
  const readAt = useRef<number | null>(null)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    if (!apiKey || busy.current) return
    busy.current = true
    const at = Date.now()
    const failed = () =>
      setSnapshot((previous) =>
        previous.status === 'ready' ? previous : { ...previous, status: 'error' }
      )
    try {
      const response = await tradingApi.getMultiQuotes(apiKey, INDEX_SYMBOLS)
      if (!alive.current) return
      if (response.status === 'success' && response.results) {
        const quotes = new Map<string, QuotesData>()
        for (const result of response.results) {
          if (result.data) quotes.set(keyOf(result.symbol, result.exchange), result.data)
        }
        readAt.current = at
        setSnapshot({ quotes, at, status: 'ready' })
      } else {
        failed()
      }
    } catch {
      if (alive.current) failed()
    } finally {
      busy.current = false
    }
  }, [apiKey])

  useEffect(() => {
    load()
  }, [load])

  const loadIfOld = useCallback(() => {
    if (readAt.current === null || Date.now() - readAt.current > SNAPSHOT_MAX_AGE_MS) load()
  }, [load])

  // NSE opening or closing.
  const lastOpen = useRef(nseOpen)
  useEffect(() => {
    const before = lastOpen.current
    lastOpen.current = nseOpen
    if (before !== null && nseOpen !== null && before !== nseOpen) load()
  }, [nseOpen, load])

  // A new connection to the feed.
  const lastEpoch = useRef(connectionEpoch)
  useEffect(() => {
    if (lastEpoch.current === connectionEpoch) return
    lastEpoch.current = connectionEpoch
    loadIfOld()
  }, [connectionEpoch, loadIfOld])

  // Back into view.
  useEffect(() => {
    const shown = () => {
      if (document.visibilityState === 'visible') loadIfOld()
    }
    document.addEventListener('visibilitychange', shown)
    return () => document.removeEventListener('visibilitychange', shown)
  }, [loadIfOld])

  return snapshot
}

function IndexTile({
  label,
  view,
  loading,
  stale,
}: {
  label: string
  view: IndexView
  loading: boolean
  stale: boolean
}) {
  const change =
    view.ltp !== null && view.prevClose !== null
      ? ((view.ltp - view.prevClose) / view.prevClose) * 100
      : null
  const direction = change === null ? 'flat' : directionOf(change)

  return (
    <li
      data-stale={stale || undefined}
      className="flex min-w-0 flex-col gap-px rounded-[0.875rem] border bg-card px-2 pt-2.5 pb-2 min-[360px]:px-2.5"
    >
      <span className="truncate text-[0.6875rem] font-semibold tracking-[0.05em] text-muted-foreground">
        {label}
      </span>
      {loading ? (
        <Skeleton className="my-1 h-5 w-[4.5rem] max-w-full" />
      ) : view.ltp !== null ? (
        <span
          className={cn(
            'sd-display text-[clamp(1rem,5.2vw,1.3125rem)] leading-[1.15] font-semibold',
            stale && 'text-muted-foreground'
          )}
        >
          {formatPrice(view.ltp)}
        </span>
      ) : (
        <span className="sd-display text-[1.3125rem] leading-[1.15] font-semibold text-muted-foreground">
          <span aria-hidden="true">&mdash;</span>
          <span className="sr-only">Not available</span>
        </span>
      )}
      <span
        className={cn(
          'flex min-h-4 items-center gap-[3px] text-xs font-bold',
          stale ? 'text-muted-foreground' : TREND_TEXT[direction]
        )}
      >
        {change !== null && (
          <>
            <TrendArrow direction={direction} className="size-2" />
            {formatPercent(change)}
          </>
        )}
        {stale && (
          <span className="ml-auto flex items-center">
            <Clock aria-hidden="true" className="size-3" />
            <span className="sr-only">Stale</span>
          </span>
        )}
      </span>
    </li>
  )
}

/**
 * NIFTY 50, NIFTY BANK and INDIA VIX, tick by tick: the level and the change on
 * the previous close, with sign and arrow. The levels stream through the one
 * market-data connection the whole app shares (`useMarketData`, LTP mode),
 * starting from a REST read that also gives the previous close. While NSE is
 * open, a level that has stopped arriving is muted and marked with a clock;
 * the line underneath says "Live" only while ticks are coming in, and
 * "Reconnecting" with the age of the levels while the socket is down (after
 * the phone wakes, say). Shown only when the account has an API key, which
 * the feed and the quote call need.
 */
export function IndexStrip({
  apiKey,
  marketOpen,
}: {
  apiKey: string | null
  marketOpen: MarketOpen
}) {
  const nseOpen = marketOpen('NSE_INDEX')
  const {
    data: prices,
    connectionEpoch,
    isAuthenticated,
    isPaused,
  } = useMarketData({
    symbols: INDEX_SYMBOLS,
    mode: 'LTP',
    enabled: apiKey !== null,
  })
  const snapshot = useIndexSnapshot(apiKey, nseOpen, connectionEpoch)
  const now = useNow()
  if (!apiKey) return null

  const rows = INDICES.map((index) => {
    const key = keyOf(index.symbol, index.exchange)
    const tick = priceOf(prices.get(key))
    return {
      ...index,
      view: indexView(snapshot.quotes.get(key), snapshot.at, prices.get(key)),
      tickAt: tick?.live ? tick.at : null,
    }
  })
  const feedTickAt = latest(...rows.map((row) => row.tickAt))
  const empty = rows.every((row) => row.view.ltp === null)

  return (
    <section aria-label="Indices">
      <ul className="grid grid-cols-3 gap-2">
        {rows.map((row) => (
          <IndexTile
            key={row.symbol}
            label={row.label}
            view={row.view}
            loading={empty && snapshot.status === 'loading'}
            stale={isStale(row.view, now, nseOpen, feedTickAt)}
          />
        ))}
      </ul>
      {empty && snapshot.status === 'error' ? (
        <p className="mt-1.5 px-1 text-xs font-medium text-muted-foreground">
          Index prices are not available right now.
        </p>
      ) : (
        <LiveLine
          className="mt-1.5 flex px-1"
          now={now}
          line={feedLine({
            now,
            marketOpen: nseOpen,
            lastTickAt: feedTickAt,
            lastUpdateAt: latest(...rows.map((row) => row.view.at)),
            connected: isAuthenticated && !isPaused,
          })}
        />
      )}
    </section>
  )
}
