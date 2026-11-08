// SUDARSHAN M7.1-M7.4: the Upstox tab, read-only. Holdings, Positions, Funds and History, each
// read by the service from Upstox on the Analytics Token and moved only by ticks between reads.
import { useQuery } from '@tanstack/react-query'
import { FlaskConical, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { useThemeStore } from '@/stores/themeStore'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import { Segments, UPSTOX_LABELS } from '../StubViews'
import { SECONDARY } from '../sheets/TabFields'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { UPSTOX_VIEWS, type UpstoxView } from '../usePortfolioOverlay'
import { FundsView } from './FundsView'
import { HistoryView } from './HistoryView'
import { HoldingsView } from './HoldingsView'
import { PositionsView } from './PositionsView'
import { CARD } from './parts'
import { PnlView, TradesView } from './ReportsView'
import { useUpstoxBook } from './useUpstoxBook'
import { useUpstoxLive } from './useUpstoxLive'

export const ANALYZE_WORDS = 'Your real Upstox account. Analyze-mode orders are not in it.'

function Waiting() {
  return (
    <p
      className="flex items-center justify-center gap-2 py-8 text-sm text-[color:var(--text-secondary)]"
      aria-live="polite"
    >
      <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
      Reading your Upstox account
    </p>
  )
}

function Failed({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div
      className={`${CARD} flex flex-col items-center gap-3 px-6 py-8 text-center`}
      aria-live="polite"
    >
      <TriangleAlert aria-hidden="true" className="size-6 text-[color:var(--text-secondary)]" />
      <p className="text-base font-bold">{problemOf(error).message}</p>
      <button type="button" className={SECONDARY} onClick={retry}>
        <RefreshCw aria-hidden="true" className="size-4" />
        Try again
      </button>
    </div>
  )
}

function BookPart({
  view,
  marketOpen,
  anyOpen,
}: {
  view: Exclude<UpstoxView, 'history' | 'trades' | 'pnl'>
  marketOpen: (exchange: string) => boolean | null
  anyOpen: () => boolean | null
}) {
  const query = useUpstoxBook(view, anyOpen)
  const book = query.data
  const live = useUpstoxLive({
    holdings: book?.holdings?.data ?? [],
    positions: book?.positions?.data ?? [],
    marketOpen,
    enabled: view === 'holdings' || view === 'positions',
  })
  if (query.isPending) return <Waiting />
  if (query.isError && !book) return <Failed error={query.error} retry={() => query.refetch()} />
  if (!book) return <Waiting />
  return (
    <>
      {query.isError && (
        <p
          aria-live="polite"
          className="mb-3 rounded-[0.875rem] border border-[color:var(--warning-text)] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-medium text-[color:var(--warning-text)]"
        >
          {problemOf(query.error).message}
        </p>
      )}
      {view === 'holdings' && <HoldingsView part={book.holdings} live={live} />}
      {view === 'positions' && (
        <PositionsView positions={book.positions} mtf={book.mtf} live={live} />
      )}
      {view === 'funds' && <FundsView part={book.funds} />}
    </>
  )
}

function HistoryPart() {
  const query = useQuery({
    queryKey: [...PORTFOLIO_KEY, 'upstox-history'],
    queryFn: async () => {
      const result = await portfolioCall('upstox_history')
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  })
  if (query.isPending) return <Waiting />
  if (query.isError) return <Failed error={query.error} retry={() => query.refetch()} />
  return <HistoryView history={query.data} />
}

export function UpstoxTab({
  view,
  onView,
}: {
  view: UpstoxView
  onView: (view: UpstoxView) => void
}) {
  const analyzer = useThemeStore((s) => s.appMode) === 'analyzer'
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen, anyOpen } = useMarketOpen(market)
  return (
    <div className="flex flex-col gap-3">
      <MarketCalendarSource key={day} onChange={setMarket} />
      <Segments views={UPSTOX_VIEWS} labels={UPSTOX_LABELS} value={view} onChange={onView} />
      {analyzer && (
        <p className="flex items-center gap-2 rounded-[0.875rem] border border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] px-3 py-2 text-sm font-medium text-[color:var(--sandbox-ink)]">
          <FlaskConical aria-hidden="true" className="size-4 shrink-0" />
          {ANALYZE_WORDS}
        </p>
      )}
      {view === 'history' ? (
        <HistoryPart />
      ) : view === 'trades' ? (
        <TradesView />
      ) : view === 'pnl' ? (
        <PnlView />
      ) : (
        <BookPart key={view} view={view} marketOpen={isOpen} anyOpen={anyOpen} />
      )}
    </div>
  )
}
