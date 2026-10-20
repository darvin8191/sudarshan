import { ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { tradingApi } from '@/api/trading'
import { calculateLiveStats, useLivePrice } from '@/hooks/useLivePrice'
import { cn } from '@/lib/utils'
import { useBrokerStore } from '@/stores/brokerStore'
import { onModeChange } from '@/stores/themeStore'
import { useBookFeed } from '@/sudarshan/books/feed'
import { holdingsMayChange, useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
import type { Holding, PortfolioStats } from '@/types/trading'
import { directionOf, formatPercent, formatRupees, toNumber } from './format'
import { LiveLine } from './LiveLine'
import { anyOpen, type MarketOpen } from './live'
import { TREND_TEXT, TrendArrow } from './Trend'

interface Summary {
  count: number
  current: number
  invested: number
  pnl: number
  percent: number
}

function summarise(count: number, stats: PortfolioStats | null): Summary | null {
  if (!stats || count === 0) return null
  const current = toNumber(stats.totalholdingvalue)
  const invested = toNumber(stats.totalinvvalue)
  const pnl = toNumber(stats.totalprofitandloss)
  const percent = toNumber(stats.totalpnlpercentage)
  if (current === null || invested === null || pnl === null || percent === null) return null
  return { count, current, invested, pnl, percent }
}

interface Book {
  holdings: Holding[]
  stats: PortfolioStats
  /** When the holdings were asked for, so only newer ticks move them. */
  at: number
}

const NOT_KNOWN: MarketOpen = () => null

/**
 * The holdings at a glance: what they are worth now, what went into them and
 * the overall P&L. Read from the broker when the dashboard opens, after every
 * order event including its own fills (order_update), on a reconnect, on
 * coming back after 15 seconds and every 45 seconds while a market is open;
 * between reads the value and P&L move with every tick, as on the Holdings
 * page (useLivePrice). Nothing is shown when there are no holdings, when the
 * broker has none to give (crypto), when there is no API key to ask with, or
 * when the call fails: the Holdings page has the detail, and its own errors.
 * There is no "today" figure, because the holdings call carries no day change.
 */
export function HoldingsCard({
  apiKey,
  marketOpen = NOT_KNOWN,
}: {
  apiKey: string | null
  marketOpen?: MarketOpen
}) {
  const supported = useBrokerStore((s) => s.capabilities?.broker_type !== 'crypto')
  const [book, setBook] = useState<Book | null>(null)
  const alive = useRef(true)
  const beginRead = useLatestRead()

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    if (!apiKey) return
    const isLatest = beginRead()
    const at = Date.now()
    try {
      const response = await tradingApi.getHoldings(apiKey)
      if (!alive.current || !isLatest()) return
      const holdings = response.data?.holdings
      const stats = response.data?.statistics
      setBook(
        response.status === 'success' && Array.isArray(holdings) && stats
          ? { holdings: holdings as Holding[], stats, at }
          : null
      )
    } catch {
      if (alive.current && isLatest()) setBook(null)
    }
  }, [apiKey, beginRead])

  useEffect(() => {
    if (supported) load()
  }, [supported, load])

  // Live and Analyze hold different books.
  useEffect(() => onModeChange(() => load()), [load])

  const holdings = useMemo(() => book?.holdings ?? [], [book])
  const anyMarketOpen = useCallback(
    () =>
      holdings.length > 0
        ? anyOpen(holdings.map((holding) => marketOpen(holding.exchange)))
        : marketOpen('NSE'),
    [holdings, marketOpen]
  )
  // Only an update that can change the holdings is answered (an equity
  // delivery fill or rejection, the sandbox's settlement), not every F&O fill.
  useBookSync(load, {
    enabled: supported && !!apiKey,
    marketOpen: anyMarketOpen,
    accept: holdingsMayChange,
  })

  const { data: priced, feed } = useLivePrice(holdings, {
    enabled: holdings.length > 0,
    snapshotAt: book?.at ?? null,
  })
  const { line, now } = useBookFeed(priced, feed, marketOpen)
  const stale = (line.kind === 'delayed' || line.kind === 'connecting') && line.stale
  const stats = useMemo(() => {
    if (!book) return null
    const moved = priced.some((row) => row._dataSource !== 'rest')
    return moved ? (calculateLiveStats(priced, book.stats) as PortfolioStats) : book.stats
  }, [book, priced])
  const summary = summarise(holdings.length, stats)

  if (!supported || !summary) return null

  const direction = directionOf(summary.pnl)

  return (
    <section
      aria-labelledby="sd-holdings-title"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="sd-holdings-title" className="flex items-center gap-2 text-[0.9375rem] font-bold">
          Holdings
          <span className="flex h-6 items-center rounded-full bg-[color:var(--brand-tint)] px-2.5 text-xs font-semibold text-[color:var(--brand)]">
            {summary.count}
          </span>
        </h2>
        <Link
          to="/holdings"
          className="-my-2 -mr-2 inline-flex min-h-12 items-center gap-0.5 rounded-lg px-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          See all
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>
      <dl className="grid grid-cols-2 gap-3">
        <div className="flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Current value</dt>
          <dd
            className={cn(
              'sd-display text-[1.3125rem] leading-[1.15] font-semibold',
              stale && 'text-muted-foreground'
            )}
          >
            {formatRupees(summary.current, { decimals: 0 })}
          </dd>
          <dd className="text-[0.71875rem] font-medium whitespace-nowrap text-[color:var(--text-tertiary)]">
            Invested {formatRupees(summary.invested, { decimals: 0 })}
          </dd>
        </div>
        <div className="flex min-w-0 flex-col gap-px">
          <dt className="text-xs font-semibold text-muted-foreground">Overall P&amp;L</dt>
          <dd
            className={cn(
              'sd-display flex items-center gap-1 text-[1.3125rem] leading-[1.15] font-semibold',
              stale ? 'text-muted-foreground' : TREND_TEXT[direction]
            )}
          >
            <TrendArrow direction={direction} className="size-[0.5625rem]" />
            {formatRupees(summary.pnl, { decimals: 0, signed: true })}
          </dd>
          <dd
            className={cn(
              'text-[0.71875rem] font-semibold',
              stale ? 'text-muted-foreground' : TREND_TEXT[directionOf(summary.percent)]
            )}
          >
            {formatPercent(summary.percent)}
          </dd>
        </div>
      </dl>
      <LiveLine line={line} now={now} className="self-start" />
    </section>
  )
}
