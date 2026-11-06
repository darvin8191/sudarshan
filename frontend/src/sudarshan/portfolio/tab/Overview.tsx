// SUDARSHAN M7.23: the current-status view of a custom tab (design 4.5 and 4.6, widgets 1-4):
// Summary, Today, Holdings and Top movers, in the order and with the columns the tab's layout
// says. The service prices the tab at read_at; ticks newer than that move it (useTabLive), so
// "Real-time" shows only while ticks arrive. A re-read comes on mount, a reconnect, a return after
// 15 seconds and any change to the portfolio, never on a timer.
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useBookSync } from '@/sudarshan/books/useBookSync'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { problemOf } from '../api'
import { CustomizeSheet } from '../sheets/CustomizeSheet'
import { SECONDARY } from '../sheets/TabFields'
import type {
  Account,
  AllocationBy,
  LayoutSpec,
  SavedView,
  TabInfo,
  TabView,
  Widget,
} from '../types'
import { ReadStamp } from '../upstox/parts'
import { useTabLive } from '../useTabLive'
import { PORTFOLIO_KEY } from './ActivityTab'
import { AllocationBars } from './AllocationBars'
import { countFilters, FilterBar, NO_FILTERS } from './Filters'
import { dayText } from './format'
import { groupRows, TAG_GROUPS_NOTE } from './groups'
import { HoldingsList } from './HoldingsList'
import { PerformanceBlock } from './PerformanceBlocks'
import { listRows, moversOf, sortRows } from './rows'
import { SummaryTile, TodayTile } from './SummaryTiles'
import { argsOf, isActive, type Narrowing, pick, sliced } from './slices'
import { TopMovers } from './TopMovers'
import { tabViewOptions } from './tabView'

/** The widgets this screen draws; the others arrive with their own parts. */
type Drawn =
  | 'summary'
  | 'today'
  | 'holdings'
  | 'movers'
  | 'allocation'
  | 'history'
  | 'performance'
  | 'income'
const DRAWN: readonly Widget[] = [
  'summary',
  'today',
  'holdings',
  'movers',
  'allocation',
  'history',
  'performance',
  'income',
]

/** The layout, with the sort, grouping and columns of a saved view where one is in use. */
function specInUse(spec: LayoutSpec, view: SavedView | null): LayoutSpec {
  if (!view) return spec
  return { ...spec, sort: view.spec.sort, group: view.spec.group, columns: view.spec.columns }
}

const BUTTON_ROW = 'flex flex-wrap justify-center gap-2'

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
  accounts,
  narrowing,
  onNarrow,
  allocBy,
  onAllocBy,
  view,
  onClearView,
  onAdd,
  onImport,
}: {
  data: TabView
  part: 'overview' | 'holdings'
  marketOpen: (exchange: string) => boolean | null
  accounts: readonly Account[]
  narrowing: Narrowing
  onNarrow: (next: Narrowing) => void
  allocBy: AllocationBy
  onAllocBy: (next: AllocationBy) => void
  /** The saved view in use: its sort, grouping and columns stand in for the layout's. */
  view: SavedView | null
  onClearView: () => void
  onAdd: () => void
  onImport: () => void
}) {
  const asOf = data.as_of !== null
  const rowsIn = useMemo(() => sliced(data.rows, narrowing.slice), [data.rows, narrowing.slice])
  // A day in the past has no ticks: the service's prices of that day stand.
  const live = useTabLive({
    rows: rowsIn,
    fixed: data.fixed,
    readAt: asOf ? Number.MAX_SAFE_INTEGER : data.read_at,
    marketOpen,
    enabled: !asOf,
  })
  const spec = specInUse(data.layout.spec, view)
  const listed = useMemo(
    () => sortRows(listRows(live.rows, live.moved, live.totals.value_paise), spec.sort),
    [live.rows, live.moved, live.totals.value_paise, spec.sort]
  )
  const movers = useMemo(() => moversOf(listed), [listed])
  const stale = (r: (typeof listed)[number]) => live.stale(r.row)
  const allStale = live.line.kind === 'delayed' && live.line.stale
  const narrowed = countFilters(narrowing.filters) > 0 || narrowing.slice !== null
  const today = data.today

  const filterBar = (
    <FilterBar
      filters={narrowing.filters}
      onChange={(filters) => onNarrow({ ...narrowing, filters })}
      accounts={accounts}
      assets={data.assets}
      today={today}
    />
  )
  if (listed.length === 0) {
    if (!narrowed) return <Empty onAdd={onAdd} onImport={onImport} />
    return (
      <div className="flex flex-col gap-3">
        {filterBar}
        <section className="flex flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]">
          <p className="text-base font-bold">
            {asOf
              ? `Nothing was held on ${dayText(data.as_of as number)}.`
              : 'No holding matches these filters.'}
          </p>
          <button
            type="button"
            className={SECONDARY}
            onClick={() => onNarrow({ filters: NO_FILTERS, slice: null })}
          >
            Clear filters
          </button>
        </section>
      </div>
    )
  }

  const groups =
    spec.group === 'none'
      ? null
      : groupRows(
          listed,
          spec.group,
          spec.sort,
          live.totals.value_paise,
          (id) => accounts.find((a) => a.id === id)?.name ?? 'An account'
        )
  const holdings = (
    <div key="holdings" className="flex flex-col gap-2">
      <HoldingsList rows={listed} columns={spec.columns} stale={stale} groups={groups} />
      {spec.group === 'tag' && (
        <p className="text-sm text-[color:var(--text-secondary)]">{TAG_GROUPS_NOTE}</p>
      )}
    </div>
  )
  // The history, performance and income are for the whole tab: a narrowed or past view has no
  // live point on the chart, and the figures are the service's.
  const liveValue = asOf || narrowed ? null : live.totals.value_paise
  const performance = (widget: 'history' | 'performance' | 'income') => (
    <PerformanceBlock
      key={widget}
      tabId={data.tab.id}
      widget={widget}
      today={today}
      liveValuePaise={liveValue}
      unpriced={asOf || narrowed ? 0 : live.totals.unpriced}
    />
  )
  const widgets: Record<Drawn, React.ReactNode> = {
    history: performance('history'),
    performance: performance('performance'),
    income: performance('income'),
    summary: <SummaryTile key="summary" totals={live.totals} fixed={data.fixed} stale={allStale} />,
    // Today means the day the page is read: not for a day in the past.
    today: asOf ? null : (
      <TodayTile key="today" totals={live.totals} fixed={data.fixed} stale={allStale} />
    ),
    holdings,
    movers: asOf ? null : <TopMovers key="movers" movers={movers} />,
    allocation: (
      <AllocationBars
        key="allocation"
        by={allocBy}
        onBy={onAllocBy}
        rows={data.allocation[allocBy] ?? []}
        isActive={(row) => isActive(allocBy, row, narrowing, accounts)}
        onPick={(row) => onNarrow(pick(allocBy, row, narrowing, accounts))}
      />
    ),
  }
  const shown =
    part === 'holdings'
      ? [holdings]
      : spec.widgets.filter((w) => DRAWN.includes(w)).map((w) => widgets[w as Drawn])
  return (
    <div className="flex flex-col gap-3">
      {filterBar}
      {view && (
        <p className="flex flex-wrap items-center gap-x-3 text-sm text-[color:var(--text-secondary)]">
          <span>
            View: <span className="font-semibold text-foreground">{view.name}</span>
          </span>
          <button
            type="button"
            onClick={onClearView}
            className="inline-flex min-h-12 items-center font-semibold text-[color:var(--brand)] underline outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            Back to the layout
          </button>
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {asOf ? (
          <span className="text-xs font-semibold text-[color:var(--text-secondary)]">
            Value as on {dayText(data.as_of as number)}
          </span>
        ) : (
          <LiveLine line={live.line} now={live.now} />
        )}
        <ReadStamp at={data.read_at} />
      </div>
      {asOf && live.totals.unpriced > 0 && (
        <p
          aria-live="polite"
          className="rounded-[0.875rem] border border-[color:var(--warning-text)] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-medium text-[color:var(--warning-text)]"
        >
          Value on that day is not known
          {live.totals.unpriced === listed.length
            ? '.'
            : ` for ${live.totals.unpriced} holding${live.totals.unpriced === 1 ? '' : 's'}.`}{' '}
          They are counted at cost.
        </p>
      )}
      {narrowing.slice !== null && (
        <p className="text-sm text-[color:var(--text-secondary)]">
          Realised gains, income and fees are for the whole tab, not this slice.
        </p>
      )}
      {!asOf && live.capped > 0 && (
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
  accounts = [],
  customizing = false,
  onCustomizeClose = () => {},
  onAdd,
  onImport,
}: {
  tab: TabInfo
  part: 'overview' | 'holdings'
  accounts?: readonly Account[]
  /** The Customize sheet is open (the address says so). */
  customizing?: boolean
  onCustomizeClose?: () => void
  onAdd: () => void
  onImport: () => void
}) {
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const queries = useQueryClient()
  const [narrowing, setNarrowing] = useState<Narrowing>({ filters: NO_FILTERS, slice: null })
  const [allocBy, setAllocBy] = useState<AllocationBy>('class')
  const query = useQuery({
    ...tabViewOptions(tab.id, argsOf(narrowing.filters)),
    placeholderData: keepPreviousData,
  })
  const { refetch } = query
  // The saved view the tab opens with, taken on once; undefined until the tab has been read.
  const [appliedId, setAppliedId] = useState<number | null | undefined>(undefined)
  const data = query.data
  useEffect(() => {
    if (appliedId !== undefined || !data) return
    const first = data.views.find((v) => v.is_default) ?? null
    setAppliedId(first?.id ?? null)
    if (first) setNarrowing({ filters: { ...NO_FILTERS, ...first.spec.filters }, slice: null })
  }, [appliedId, data])
  const applied = data?.views.find((v) => v.id === appliedId) ?? null
  const useView = (next: SavedView | null) => {
    setAppliedId(next?.id ?? null)
    if (next) setNarrowing({ filters: { ...NO_FILTERS, ...next.spec.filters }, slice: null })
  }
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
      <Loaded
        data={query.data}
        part={part}
        marketOpen={isOpen}
        accounts={accounts}
        narrowing={narrowing}
        onNarrow={setNarrowing}
        allocBy={allocBy}
        onAllocBy={setAllocBy}
        view={applied}
        onClearView={() => setAppliedId(null)}
        onAdd={onAdd}
        onImport={onImport}
      />
    )
  }
  return (
    <>
      <MarketCalendarSource key={day} onChange={setMarket} />
      {body}
      {data && (
        <CustomizeSheet
          open={customizing}
          onClose={onCustomizeClose}
          tab={tab}
          layout={data.layout}
          views={data.views}
          filters={narrowing.filters}
          drawn={DRAWN}
          appliedViewId={applied?.id ?? null}
          onUseView={useView}
          onChanged={() => void queries.invalidateQueries({ queryKey: PORTFOLIO_KEY })}
        />
      )}
    </>
  )
}
