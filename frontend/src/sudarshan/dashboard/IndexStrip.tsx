import { useCallback, useEffect, useRef, useState } from 'react'
import { type QuotesData, tradingApi } from '@/api/trading'
import { Skeleton } from '@/components/ui/skeleton'
import { useMarketStatus } from '@/hooks/useMarketStatus'
import { usePageVisibility } from '@/hooks/usePageVisibility'
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatPrice } from './format'
import { TREND_TEXT, TrendArrow } from './Trend'

/** The three the dashboard shows, in OpenAlgo's symbols for them. */
export const INDICES = [
  { symbol: 'NIFTY', exchange: 'NSE_INDEX', label: 'NIFTY 50' },
  { symbol: 'BANKNIFTY', exchange: 'NSE_INDEX', label: 'NIFTY BANK' },
  { symbol: 'INDIAVIX', exchange: 'NSE_INDEX', label: 'INDIA VIX' },
] as const

/** How often the quotes are read again while NSE is open and the page is in view. */
export const INDEX_REFRESH_MS = 30_000

const keyOf = (symbol: string, exchange: string) => `${exchange}:${symbol}`

type Status = 'loading' | 'ready' | 'error'

/**
 * The index quotes, from the multi-quote REST call the rest of the app already
 * uses, never the market-data socket: the proxy opens the broker's feed as
 * soon as a client connects, and the dashboard is the page left open all day,
 * so a socket here would hold one of the account's few Upstox feed slots from
 * login onwards. One call of three keys every 30 seconds sits far inside the
 * broker's quote limits. Read once on arrival and whenever the page comes back
 * into view; refreshed only while NSE is open, since a closed market's levels
 * do not move.
 */
export function useIndexQuotes(apiKey: string | null) {
  const [quotes, setQuotes] = useState<Map<string, QuotesData>>(() => new Map())
  const [status, setStatus] = useState<Status>('loading')
  const { isVisible } = usePageVisibility()
  const { isMarketOpen } = useMarketStatus()
  const marketOpen = useRef(isMarketOpen)
  const alive = useRef(true)

  useEffect(() => {
    marketOpen.current = isMarketOpen
  }, [isMarketOpen])

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    if (!apiKey) return
    try {
      const response = await tradingApi.getMultiQuotes(
        apiKey,
        INDICES.map(({ symbol, exchange }) => ({ symbol, exchange }))
      )
      if (!alive.current) return
      if (response.status === 'success' && response.results) {
        const next = new Map<string, QuotesData>()
        for (const result of response.results) {
          if (result.data) next.set(keyOf(result.symbol, result.exchange), result.data)
        }
        setQuotes(next)
        setStatus('ready')
      } else {
        // Keep what is on screen; say so only if there was never anything.
        setStatus((previous) => (previous === 'ready' ? previous : 'error'))
      }
    } catch {
      if (alive.current) setStatus((previous) => (previous === 'ready' ? previous : 'error'))
    }
  }, [apiKey])

  // On arrival, and each time the page comes back into view.
  useEffect(() => {
    if (apiKey && isVisible) load()
  }, [apiKey, isVisible, load])

  // While in view: every 30 seconds, but only while NSE is open. The check is
  // made at each tick, so the first refresh after 09:15 needs no re-render.
  useEffect(() => {
    if (!apiKey || !isVisible) return
    const timer = setInterval(() => {
      if (marketOpen.current('NSE_INDEX')) load()
    }, INDEX_REFRESH_MS)
    return () => clearInterval(timer)
  }, [apiKey, isVisible, load])

  return { quotes, status }
}

function IndexTile({
  label,
  quote,
  loading,
}: {
  label: string
  quote: QuotesData | undefined
  loading: boolean
}) {
  const ltp = quote && quote.ltp > 0 ? quote.ltp : null
  const previous = quote && quote.prev_close > 0 ? quote.prev_close : null
  const change = ltp !== null && previous !== null ? ((ltp - previous) / previous) * 100 : null
  const direction = change === null ? 'flat' : directionOf(change)

  return (
    <li className="flex min-w-0 flex-col gap-px rounded-[0.875rem] border bg-card px-2 pt-2.5 pb-2 min-[360px]:px-2.5">
      <span className="truncate text-[0.6875rem] font-semibold tracking-[0.05em] text-muted-foreground">
        {label}
      </span>
      {loading ? (
        <Skeleton className="my-1 h-5 w-[4.5rem] max-w-full" />
      ) : ltp !== null ? (
        <span className="sd-display text-[clamp(1rem,5.2vw,1.3125rem)] leading-[1.15] font-semibold">
          {formatPrice(ltp)}
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
          TREND_TEXT[direction]
        )}
      >
        {change !== null && (
          <>
            <TrendArrow direction={direction} className="size-2" />
            {formatPercent(change)}
          </>
        )}
      </span>
    </li>
  )
}

/**
 * NIFTY 50, NIFTY BANK and INDIA VIX: the level and the change on the previous
 * close, with sign and arrow. Shown only when the account has an API key, which
 * the quote call needs; no intraday line, because no intraday series is read.
 */
export function IndexStrip({ apiKey }: { apiKey: string | null }) {
  const { quotes, status } = useIndexQuotes(apiKey)
  if (!apiKey) return null

  return (
    <section aria-label="Indices">
      <ul className="grid grid-cols-3 gap-2">
        {INDICES.map((index) => (
          <IndexTile
            key={index.symbol}
            label={index.label}
            quote={quotes.get(keyOf(index.symbol, index.exchange))}
            loading={status === 'loading'}
          />
        ))}
      </ul>
      {status === 'error' && (
        <p className="mt-1.5 px-1 text-xs font-medium text-muted-foreground">
          Index prices are not available right now.
        </p>
      )}
    </section>
  )
}
