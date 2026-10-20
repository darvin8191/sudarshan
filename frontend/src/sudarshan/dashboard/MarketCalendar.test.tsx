/**
 * The market calendar the live dashboard trusts: read again each day, and
 * "not known" rather than "closed" whenever it cannot say.
 */
import { act, render, renderHook, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const calendar = vi.hoisted(() => ({
  reads: 0,
  answer: {
    isLoading: false,
    error: null as string | null,
    isMarketOpen: (_exchange: string) => true,
    isAnyMarketOpen: () => true,
  },
}))
vi.mock('@/hooks/useMarketStatus', () => ({
  // Counts each mount, which is when the real hook reads the timings.
  useMarketStatus: () => {
    useEffect(() => {
      calendar.reads += 1
    }, [])
    return calendar.answer
  },
}))

import { useMarketOpen } from './hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from './MarketCalendar'

function Probe({ onChange }: { onChange: (calendar: MarketCalendar) => void }) {
  const day = useIstDay()
  return (
    <>
      <span data-testid="day">{day}</span>
      <MarketCalendarSource key={day} onChange={onChange} />
    </>
  )
}

afterEach(() => {
  vi.useRealTimers()
  calendar.reads = 0
})

describe('MarketCalendarSource', () => {
  it('reads the calendar again after midnight in India, on a page left open', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.UTC(2026, 9, 20, 18, 29, 30)) // 23:59:30 IST
    const onChange = vi.fn()
    render(<Probe onChange={onChange} />)
    expect(screen.getByTestId('day')).toHaveTextContent('2026-10-20')
    expect(calendar.reads).toBe(1)
    expect(onChange).toHaveBeenLastCalledWith(calendar.answer)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000)
    })
    expect(screen.getByTestId('day')).toHaveTextContent('2026-10-21')
    expect(calendar.reads).toBe(2)
  })
})

describe('useMarketOpen', () => {
  const open: MarketCalendar = {
    isLoading: false,
    error: null,
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
  }

  it('knows nothing while the calendar loads, or when it could not be read', () => {
    expect(renderHook(() => useMarketOpen(CALENDAR_LOADING)).result.current.isOpen('NSE')).toBe(
      null
    )
    const failed = renderHook(() => useMarketOpen({ ...open, error: 'Failed' }))
    expect(failed.result.current.isOpen('NSE')).toBeNull()
    expect(failed.result.current.anyOpen()).toBeNull()
  })

  it('passes on the answer once the calendar is read', () => {
    const { result } = renderHook(() =>
      useMarketOpen({ ...open, isMarketOpen: (exchange) => exchange === 'NSE' })
    )
    expect(result.current.isOpen('NSE')).toBe(true)
    expect(result.current.isOpen('MCX')).toBe(false)
    expect(result.current.anyOpen()).toBe(true)
  })

  it('knows nothing from a calendar read on an earlier day', () => {
    vi.useFakeTimers()
    vi.setSystemTime(Date.UTC(2026, 9, 20, 18, 0)) // 23:30 IST
    const { result, rerender } = renderHook(() => useMarketOpen(open))
    expect(result.current.isOpen('NSE')).toBe(true)

    vi.setSystemTime(Date.UTC(2026, 9, 20, 19, 0)) // 00:30 IST, the next day
    rerender()
    expect(result.current.isOpen('NSE')).toBeNull()
  })
})
