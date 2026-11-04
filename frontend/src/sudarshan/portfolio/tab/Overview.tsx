// SUDARSHAN M7.23: the current-status view of a custom tab (design 4.5 and 4.6, widgets 1-4):
// Summary, Today, Holdings and Top movers, in the order and with the columns the tab's layout
// says. The service prices the tab at read_at; ticks newer than that move it (useTabLive), so
// "Real-time" shows only while ticks arrive. A re-read comes on mount, a reconnect, a return after
// 15 seconds and any change to the portfolio, never on a timer.
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useBookSync } from '@/sudarshan/books/useBookSync'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import { SECONDARY } from '../sheets/TabFields'
import type { TabInfo, TabView, Widget } from '../types'
import { ReadStamp } from '../upstox/parts'
import { useTabLive } from '../useTabLive'
import { PORTFOLIO_KEY } from './ActivityTab'
import { HoldingsList } from './HoldingsList'
import { listRows, moversOf, sortRows } from './rows'
import { SummaryTile, TodayTile } from './SummaryTiles'
import { TopMovers } from './TopMovers'

/** The widgets this screen draws; the others arrive with their own parts. */
type Drawn = 'summary' | 'today' | 'holdings' | 'movers'
const DRAWN: readonly Widget[] = ['summary', 'today', 'holdings', 'movers']

const BUTTON_ROW = 'flex flex-wrap justify-center gap-2'

export const TAB_VIEW_KEY = [...PORTFOLIO_KEY, 'tab-view'] as const

function Empty({ onAdd, onImport }: { onAdd: () => void; onImport: () => void }) {
  return (
    <section className="flex flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]">
      <p className="text-base font-bold">Nothing here yet. Add an entry or import a file.</p>
      <div className={BUTTON_ROW}>
        <button type="button" className={SECONDARY} onClick={onAdd}>
          Add an entry
        </button>
        <button type="button" className={SECONDARY} onClick={onImport}>
          Import a file
        </button>
      </div>
    </section>
  )
}

function Loaded({
  data,
  part,
  marketOpen,
  onAdd,
  onImport,
}: {
  data: TabView
  part: 'overview' | 'holdings'
  marketOpen: (exchange: string) => boolean | null
  onAdd: () => void
  onImport: () => void
}) {
  const live = useTabLive({
    rows: data.rows,
    fixed: data.fixed,
    readAt: data.read_at,
    marketOpen,
  })
  const spec = data.layout.spec
  const listed = useMemo(
    () => sortRows(listRows(live.rows, live.moved, live.totals.value_paise), spec.sort),
    [live.rows, live.moved, live.totals.value_paise, spec.sort]
  )
  const movers = useMemo(() => moversOf(listed), [listed])
  const stale = (r: (typeof listed)[number]) => live.stale(r.row)
  const allStale = live.line.kind === 'delayed' && live.line.stale

  if (listed.length === 0) return <Empty onAdd={onAdd} onImport={onImport} />

  const holdings = (
    <HoldingsList key="holdings" rows={listed} columns={spec.columns} stale={stale} />
  )
  const widgets: Record<Drawn, React.ReactNode> = {
    summary: <SummaryTile key="summary" totals={live.totals} fixed={data.fixed} stale={allStale} />,
    today: <TodayTile key="today" totals={live.totals} fixed={data.fixed} stale={allStale} />,
    holdings,
    movers: <TopMovers key="movers" movers={movers} />,
  }
  const shown =
    part === 'holdings'
      ? [holdings]
      : spec.widgets.filter((w) => DRAWN.includes(w)).map((w) => widgets[w as Drawn])
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LiveLine line={live.line} now={live.now} />
        <ReadStamp at={data.read_at} />
      </div>
      {live.capped > 0 && (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {live.capped} holding{live.capped === 1 ? '' : 's'} beyond the first 200 show the price of
          the last read, not a real-time one.
        </p>
      )}
      {shown}
    </div>
  )
}

export function TabOverview({
  tab,
  part,
  onAdd,
  onImport,
}: {
  tab: TabInfo
  part: 'overview' | 'holdings'
  onAdd: () => void
  onImport: () => void
}) {
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const query = useQuery({
    queryKey: [...TAB_VIEW_KEY, tab.id],
    queryFn: async (): Promise<TabView> => {
      const result = await portfolioCall('tab_get', { id: tab.id })
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  })
  const { refetch } = query
  // No timer: a reconnect and a return after 15 seconds read again.
  useBookSync(() => refetch(), { events: [], resyncMs: null })

  let body: React.ReactNode
  if (query.isPending) {
    body = (
      <p
        className="flex items-center justify-center gap-2 py-8 text-sm text-[color:var(--text-secondary)]"
        aria-live="polite"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading this tab
      </p>
    )
  } else if (query.isError) {
    body = (
      <div
        className="flex flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]"
        aria-live="polite"
      >
        <TriangleAlert aria-hidden="true" className="size-6 text-[color:var(--text-secondary)]" />
        <p className="text-base font-bold">{problemOf(query.error).message}</p>
        <button type="button" className={SECONDARY} onClick={() => refetch()}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  } else {
    body = (
      <Loaded data={query.data} part={part} marketOpen={isOpen} onAdd={onAdd} onImport={onImport} />
    )
  }
  return (
    <>
      <MarketCalendarSource key={day} onChange={setMarket} />
      {body}
    </>
  )
}
