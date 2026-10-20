import { useCallback, useEffect, useState } from 'react'
import { istDay, type MarketOpen } from './live'
import type { MarketCalendar } from './MarketCalendar'

/**
 * The time now, moved on every `stepMs` while the page is in view, so that an
 * age ("Updated 12s ago") or a stale mark is re-read even when no tick comes to
 * prompt it. A hidden page skips the re-render; the first step after it comes
 * back catches up.
 */
export function useNow(stepMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') setNow(Date.now())
    }, stepMs)
    return () => clearInterval(timer)
  }, [stepMs])
  return now
}

/**
 * The market calendar `useMarketStatus` reads, as three answers instead of two.
 * Its `isMarketOpen` says "closed" while the timings are still loading, when
 * they could not be read, and for timings read on an earlier day (the
 * dashboard reads the calendar again each day, `MarketCalendarSource`, and
 * this covers the moments before that read lands). A live view must not call
 * the market closed on any of those grounds, so each of them is "not known"
 * (null) here, and only a known open counts as open.
 */
export function useMarketOpen(market: MarketCalendar): {
  isOpen: MarketOpen
  anyOpen: () => boolean | null
} {
  const { isLoading, error, isMarketOpen, isAnyMarketOpen } = market
  const [readOn, setReadOn] = useState<string | null>(null)

  useEffect(() => {
    if (!isLoading) setReadOn(istDay(Date.now()))
  }, [isLoading])

  const known = useCallback(
    () => !isLoading && !error && readOn === istDay(Date.now()),
    [isLoading, error, readOn]
  )
  const isOpen = useCallback<MarketOpen>(
    (exchange) => (known() ? isMarketOpen(exchange) : null),
    [known, isMarketOpen]
  )
  const anyOpen = useCallback(() => (known() ? isAnyMarketOpen() : null), [known, isAnyMarketOpen])
  return { isOpen, anyOpen }
}
