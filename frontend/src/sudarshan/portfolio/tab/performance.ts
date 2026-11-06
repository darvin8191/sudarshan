// The performance report of a tab (the `performance` op), one query shared by the Overview's
// history, performance and income widgets and by the Performance part, plus the pure pieces the
// value chart needs. The report is whole-tab: filters never narrow it.
import { PortfolioError, portfolioCall } from '../api'
import type { Day, PerformanceReport, SeriesPoint } from '../types'
import { PORTFOLIO_KEY } from './ActivityTab'

export const PERFORMANCE_KEY = [...PORTFOLIO_KEY, 'performance'] as const

export function performanceOptions(id: number) {
  return {
    queryKey: [...PERFORMANCE_KEY, id] as const,
    queryFn: async (): Promise<PerformanceReport> => {
      const result = await portfolioCall('performance', { id })
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  }
}

/**
 * The series with today drawn from the live value: today's point is replaced by it, or added
 * after the last day. Nothing is added for a day the series already passed, or with no live value.
 */
export function withToday(
  series: readonly SeriesPoint[],
  today: Day,
  liveValuePaise: number | null,
  netPutInPaise: number
): SeriesPoint[] {
  if (liveValuePaise === null) return [...series]
  const last = series[series.length - 1]
  const point = { day: today, value_paise: liveValuePaise, net_put_in_paise: netPutInPaise }
  if (last === undefined) return []
  if (last.day === today) return [...series.slice(0, -1), { ...last, value_paise: liveValuePaise }]
  if (last.day < today) return [...series, point]
  return [...series]
}

/** 20261104 to the chart's day text, "2026-11-04". */
export function chartDay(day: Day): string {
  const y = Math.floor(day / 10000)
  const m = Math.floor(day / 100) % 100
  return `${y}-${String(m).padStart(2, '0')}-${String(day % 100).padStart(2, '0')}`
}
