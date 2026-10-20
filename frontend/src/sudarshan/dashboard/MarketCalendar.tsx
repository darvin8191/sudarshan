import { useEffect, useState } from 'react'
import { useMarketStatus } from '@/hooks/useMarketStatus'
import { istDay } from './live'

/** The parts of `useMarketStatus` the dashboard reads. */
export type MarketCalendar = Pick<
  ReturnType<typeof useMarketStatus>,
  'isLoading' | 'error' | 'isMarketOpen' | 'isAnyMarketOpen'
>

/** What the dashboard knows before the calendar has been read: nothing. */
export const CALENDAR_LOADING: MarketCalendar = {
  isLoading: true,
  error: null,
  isMarketOpen: () => false,
  isAnyMarketOpen: () => false,
}

/**
 * Today's date in India, which changes at midnight IST. Checked once a minute
 * and whenever the page comes back into view; an unchanged date re-renders
 * nothing.
 */
export function useIstDay(): string {
  const [day, setDay] = useState(() => istDay(Date.now()))
  useEffect(() => {
    const check = () => setDay(istDay(Date.now()))
    const timer = setInterval(check, 60_000)
    document.addEventListener('visibilitychange', check)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', check)
    }
  }, [])
  return day
}

/**
 * Reads the market calendar through upstream's `useMarketStatus` and hands it
 * up. The hook reads its timings once, for the day it runs on, so the
 * dashboard mounts this keyed by the date in India: a page left open overnight
 * reads the new day's calendar instead of calling every market closed all day.
 */
export function MarketCalendarSource({
  onChange,
}: {
  onChange: (calendar: MarketCalendar) => void
}) {
  const { isLoading, error, isMarketOpen, isAnyMarketOpen } = useMarketStatus()
  useEffect(() => {
    onChange({ isLoading, error, isMarketOpen, isAnyMarketOpen })
  }, [onChange, isLoading, error, isMarketOpen, isAnyMarketOpen])
  return null
}
