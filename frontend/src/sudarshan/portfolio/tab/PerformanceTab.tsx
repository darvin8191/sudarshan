// SUDARSHAN M7.30: the Performance part of a custom tab (design 4.7): the return, the value chart
// and the income, always for the whole tab. Today on the chart follows the live value, which comes
// from the same read of the tab and the same ticks as the Overview's.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useBookSync } from '@/sudarshan/books/useBookSync'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { dayOfInstant } from '../days'
import type { TabInfo, TabView } from '../types'
import { useTabLive } from '../useTabLive'
import { PerformanceBlock } from './PerformanceBlocks'
import { tabViewOptions } from './tabView'

function Live({
  tab,
  data,
  marketOpen,
}: {
  tab: TabInfo
  data: TabView | undefined
  marketOpen: (e: string) => boolean | null
}) {
  const live = useTabLive({
    rows: data?.rows ?? [],
    fixed: data?.fixed ?? {
      realised_paise: 0,
      income_paise: 0,
      fees_paise: 0,
      money_in_paise: 0,
      money_out_paise: 0,
      closed_today_paise: 0,
      closed_today_base_paise: 0,
      liabilities_paise: 0,
    },
    readAt: data?.read_at ?? Number.MAX_SAFE_INTEGER,
    marketOpen,
    enabled: data !== undefined,
  })
  const today = data?.today ?? dayOfInstant(Date.now())
  const value = data === undefined ? null : live.totals.value_paise
  return (
    <div className="flex flex-col gap-3">
      <PerformanceBlock
        tabId={tab.id}
        widget="performance"
        today={today}
        liveValuePaise={value}
        unpriced={data === undefined ? 0 : live.totals.unpriced}
      />
      <PerformanceBlock tabId={tab.id} widget="history" today={today} liveValuePaise={value} />
      <PerformanceBlock tabId={tab.id} widget="income" today={today} liveValuePaise={value} />
    </div>
  )
}

export function PerformanceTab({ tab }: { tab: TabInfo }) {
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const query = useQuery({ ...tabViewOptions(tab.id, {}), placeholderData: keepPreviousData })
  const { refetch } = query
  useBookSync(() => refetch(), { events: [], resyncMs: null })
  return (
    <>
      <MarketCalendarSource key={day} onChange={setMarket} />
      <Live tab={tab} data={query.data} marketOpen={isOpen} />
    </>
  )
}
