import { useMemo } from 'react'
import type { useMarketStatus } from '@/hooks/useMarketStatus'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { type FeedSummary, useBookFeed } from './feed'

/**
 * The status line for one instrument's quote, as in the order dialogs:
 * "Real-time", "Reconnecting", "Updated 12s ago" or "Market closed". Its own
 * component so the once-a-second age only re-renders this line, and only
 * while the dialog showing it is open.
 */
export function QuoteStatus({
  exchange,
  feed,
  calendar,
  className,
}: {
  exchange: string
  feed: FeedSummary
  calendar: Pick<
    ReturnType<typeof useMarketStatus>,
    'isLoading' | 'error' | 'isMarketOpen' | 'isAnyMarketOpen'
  >
  className?: string
}) {
  const { isOpen } = useMarketOpen(calendar)
  const rows = useMemo(() => [{ exchange }], [exchange])
  const { line, now } = useBookFeed(rows, feed, isOpen)
  return <LiveLine line={line} now={now} className={className} />
}
