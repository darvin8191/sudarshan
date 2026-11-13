import { Clock } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { useMarketStatus } from '@/hooks/useMarketStatus'
import { watchIstDay } from '@/lib/trading/dayWatch'
import { instrumentSessionFor } from '@/lib/trading/instrumentSession'
import { cn } from '@/lib/utils'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { type FeedSummary, useBookFeed } from './feed'

/**
 * The status line for one instrument's quote, as in the order dialogs:
 * "Real-time", "Reconnecting", "Updated 12s ago" or "Market closed". Its own
 * component so the once-a-second age only re-renders this line, and only
 * while the dialog showing it is open.
 *
 * A contract that has not traded today (no tick, and the day's volume is 0)
 * has no price to be late with: it says "No trade yet", not "Stale". An
 * expiring contract past its own stop on its expiry day (15:30 for an index
 * contract from 7 Dec 2026, from the server) says it stopped trading then,
 * never "Stale" and never a market that is still open (review M3).
 */
export function QuoteStatus({
  exchange,
  symbol,
  feed,
  calendar,
  className,
}: {
  exchange: string
  /** The contract, so an expiring one is said to have stopped at its own stop. */
  symbol?: string
  feed: FeedSummary & { noTrade?: boolean }
  calendar: Pick<
    ReturnType<typeof useMarketStatus>,
    'isLoading' | 'error' | 'isMarketOpen' | 'isAnyMarketOpen'
  >
  className?: string
}) {
  const { isOpen } = useMarketOpen(calendar)
  const rows = useMemo(() => [{ exchange }], [exchange])
  const { line, now } = useBookFeed(rows, feed, isOpen)
  const stopsAt = useContractStop(symbol, exchange)
  if (stopsAt !== null && now >= stopsAt)
    return (
      <LiveLine line={{ kind: 'closed', stoppedAt: stopsAt }} now={now} className={className} />
    )
  if (feed.noTrade && line.kind === 'delayed') {
    return (
      <span
        data-feed="no-trade"
        className={cn(
          'inline-flex items-center gap-1.5 text-xs font-semibold whitespace-nowrap text-muted-foreground',
          className
        )}
      >
        <Clock aria-hidden="true" className="size-3.5 shrink-0" />
        No trade yet
      </span>
    )
  }
  return <LiveLine line={line} now={now} className={className} />
}

/**
 * When an F&O contract stops trading today (epoch ms), from the server; null when it trades on.
 * A dialog left open past midnight in India asks again for the new day (review N3).
 */
function useContractStop(symbol: string | undefined, exchange: string): number | null {
  const [stopsAt, setStopsAt] = useState<number | null>(null)
  useEffect(() => {
    setStopsAt(null)
    if (!symbol || (exchange !== 'NFO' && exchange !== 'BFO')) return
    let alive = true
    const ask = () => {
      void instrumentSessionFor(symbol, exchange).then((found) => {
        if (alive) setStopsAt(found?.stopsAt ?? null)
      })
    }
    ask()
    const stopWatching = watchIstDay(ask)
    return () => {
      alive = false
      stopWatching()
    }
  }, [symbol, exchange])
  return stopsAt
}
