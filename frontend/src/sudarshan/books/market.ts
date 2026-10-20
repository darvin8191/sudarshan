import { useMarketStatus } from '@/hooks/useMarketStatus'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import type { MarketOpen } from '@/sudarshan/dashboard/live'

/**
 * The market calendar as three answers (open, closed, not known), for a page
 * that has no `useLivePrice` to borrow it from. Not known while the timings
 * load, when they fail, or when they were read on an earlier day, so a page
 * left open overnight never calls an open market closed.
 */
export function useMarketCalendar(): { isOpen: MarketOpen; anyOpen: () => boolean | null } {
  return useMarketOpen(useMarketStatus())
}
