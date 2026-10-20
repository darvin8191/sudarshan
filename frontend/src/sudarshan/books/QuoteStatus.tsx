import { Clock } from 'lucide-react'
import { useMemo } from 'react'
import type { useMarketStatus } from '@/hooks/useMarketStatus'
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
 * has no price to be late with: it says "No trade yet", not "Stale".
 */
export function QuoteStatus({
  exchange,
  feed,
  calendar,
  className,
}: {
  exchange: string
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
