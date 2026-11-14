// SUDARSHAN M14, JD2: the Trades part. Open trades first with their P&L moving with every tick,
// then closed trades by day, newest first. The service's list is the first snapshot and a re-sync
// (useJournalSync); between reads the figures of an open trade move only with ticks.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, LoaderCircle, NotebookPen, RefreshCw, TriangleAlert } from 'lucide-react'
import { useCallback, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { usePriceAccess } from '@/sudarshan/access'
import { useMarketOpen, useNow } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { describe } from '@/sudarshan/watchlist/rows'
import { journalData, problemOf } from './api'
import { JOURNAL_KEY } from './keys'
import {
  type DayGroup,
  dayWords,
  excursionBar,
  groupByDay,
  holdWords,
  openQty,
  priceText,
  strategyOf,
} from './live'
import { paiseText } from './money'
import { OutcomeWord, SideChip, StrategyChip } from './parts'
import { MistakeCount } from './tags'
import {
  dayBefore,
  overNewest,
  readClosedPage,
  type TripRow,
  type TripsRead,
  withEarlier,
} from './tripsPaging'
import type { Mode, Tag, Trip } from './types'
import { useJournalSync } from './useJournalSync'
import { type LivePnl, useTripLive } from './useTripLive'

export const EMPTY_WORDS = 'No trades yet. Fills are recorded here as they happen at Upstox.'
export const NO_TOKEN_WORDS =
  'Fills are recorded when Upstox reports them; sign in to Upstox for today’s to show at once. The day’s close check, after the market closes, records them in any case.'

const CARD = 'rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]'

function ExcursionBar({ trip }: { trip: Trip }) {
  const bar = excursionBar(trip)
  if (!bar || trip.mae_paise === null || trip.mfe_paise === null) return null
  const words = `Worst ${paiseText(trip.mae_paise)}, best ${paiseText(trip.mfe_paise)}`
  return (
    <span
      role="img"
      aria-label={words}
      title={words}
      data-testid="excursion-bar"
      className="relative block h-2 w-16 shrink-0 rounded-full bg-[color:var(--divider)]"
    >
      <span
        aria-hidden="true"
        className="absolute inset-y-[-2px] w-px bg-[color:var(--text-tertiary)]"
        style={{ left: `${bar.zeroAt}%` }}
      />
      <span
        aria-hidden="true"
        className="absolute top-[-2px] size-3 -translate-x-1/2 rounded-full border-2 border-white bg-[color:var(--text-primary)]"
        style={{ left: `${bar.netAt}%` }}
      />
    </span>
  )
}

/**
 * The contract as the books and the watchlist name it (review H5): "NIFTY 25000 CE" with its side,
 * over "NFO · 17 Nov 2026" beside the figure. Nothing is cut: the name wraps under its own width,
 * and the figure moves under the detail line when the two do not fit side by side.
 */
function Contract({ trip, children }: { trip: Trip; children: React.ReactNode }) {
  const named = describe(trip.symbol, trip.exchange)
  return (
    <>
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span
          data-testid="trip-name"
          title={trip.symbol}
          className="min-w-0 text-base leading-tight font-bold [overflow-wrap:anywhere]"
        >
          {named.title}
        </span>
        <SideChip side={trip.side} />
      </span>
      <span className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-xs font-medium text-[color:var(--text-tertiary)]">
          {named.detail}
        </span>
        <span className="ml-auto">{children}</span>
      </span>
    </>
  )
}

function OpenCard({
  trip,
  live,
  now,
  onOpen,
}: {
  trip: TripRow
  live: LivePnl | undefined
  now: number
  onOpen: (id: number) => void
}) {
  const qty = openQty(trip)
  const strategy = strategyOf(trip)
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(trip.id)}
        data-testid="open-trip"
        className={cn(
          CARD,
          'flex min-h-12 w-full flex-col gap-1.5 px-4 py-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
        )}
      >
        <Contract trip={trip}>
          {live?.pnl_paise !== null && live?.pnl_paise !== undefined ? (
            <span
              className="inline-flex items-baseline gap-1.5 text-base font-bold whitespace-nowrap"
              data-testid="live-pnl"
            >
              <Signed paise={live.pnl_paise} stale={live.stale} />
              <OutcomeWord paise={live.pnl_paise} />
              {trip.exit_qty > 0 && (
                // A part-closed trade's figure is what the open quantity makes now, not what the
                // closed part made (review M7).
                <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
                  on {qty} open
                </span>
              )}
            </span>
          ) : (
            <span className="text-sm font-semibold whitespace-nowrap text-[color:var(--text-tertiary)]">
              Waiting for a price
            </span>
          )}
        </Contract>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          <span>
            {qty} qty at {priceText(trip.entry_avg_u)}
          </span>
          <span className="inline-flex items-center gap-1">
            <Clock aria-hidden="true" className="size-3.5" />
            Held {holdWords(now - trip.opened_ms)}
          </span>
          <StrategyChip name={strategy} />
        </span>
        {trip.exit_qty > 0 && (
          <span className="text-xs font-medium text-[color:var(--text-tertiary)]">
            Part closed: {trip.exit_qty} of {trip.entry_qty} qty
          </span>
        )}
      </button>
    </li>
  )
}

function ClosedCard({
  trip,
  tags,
  onOpen,
}: {
  trip: TripRow
  tags: readonly Tag[]
  onOpen: (id: number) => void
}) {
  const net = trip.net_paise
  const strategy = strategyOf(trip)
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(trip.id)}
        data-testid="closed-trip"
        className={cn(
          CARD,
          'flex min-h-12 w-full flex-col gap-1.5 px-4 py-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
        )}
      >
        <Contract trip={trip}>
          {net === null ? (
            <span className="text-sm font-semibold whitespace-nowrap text-[color:var(--text-tertiary)]">
              Net not worked out yet
            </span>
          ) : (
            <span
              className="inline-flex items-baseline gap-1.5 text-base font-bold whitespace-nowrap"
              data-testid="trip-net"
            >
              <Signed paise={net} />
              <OutcomeWord paise={net} show={net === 0} />
              {trip.charges_actual_paise === null && (
                <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
                  est.
                </span>
              )}
            </span>
          )}
        </Contract>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {trip.hold_s !== null && (
            <span className="inline-flex items-center gap-1">
              <Clock aria-hidden="true" className="size-3.5" />
              Held {holdWords(trip.hold_s * 1000)}
            </span>
          )}
          <StrategyChip name={strategy} />
          <ExcursionBar trip={trip} />
          <MistakeCount tags={tags} />
        </span>
      </button>
    </li>
  )
}

/**
 * A day's line over its trades: the count and the day's net. A day not read whole (more trades on
 * it than the list could read) says "so far" and shows no total, never a sum of part of it. The
 * total carries "est." while a net in it rests on estimated charges, and names the trades whose net
 * is not worked out yet, which it leaves out (review M7).
 */
function DayHeader({ group, partial }: { group: DayGroup<TripRow>; partial: boolean }) {
  const count = group.trips.length
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5 px-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
      <span>{dayWords(group.day)}</span>
      <span className="ml-auto inline-flex flex-wrap items-baseline justify-end gap-x-2">
        <span>
          {count} {count === 1 ? 'trade' : 'trades'}
          {partial && ' so far'}
        </span>
        {!partial && group.net_paise !== null && (
          <span className="inline-flex items-baseline gap-1.5" data-testid="day-net">
            <Signed paise={group.net_paise} />
            {group.estimated && (
              <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
                est.
              </span>
            )}
          </span>
        )}
        {group.missing > 0 && (
          <span className="font-medium text-[color:var(--text-tertiary)]">
            {group.missing === group.trips.length
              ? 'net not worked out yet'
              : `${group.missing} not worked out yet`}
          </span>
        )}
      </span>
    </div>
  )
}

/**
 * The trades of one mode: open and closed, read from the journal. Closed trades come in whole days
 * and every page read is kept in the query's cache, so a re-read of the newest page never drops a
 * trade read before it (tripsPaging.ts, reviews H1 and M2). Each mode has its own entry: nothing
 * read for one is shown for the other.
 */
export function useTrips(mode: Mode) {
  const queryClient = useQueryClient()
  const key = useMemo(() => [...JOURNAL_KEY, 'trips', mode] as const, [mode])
  // The earlier page's sentence and wait belong to the mode they were asked in.
  const [paging, setPaging] = useState<{ mode: Mode; message: string } | null>(null)
  const [loading, setLoading] = useState<Mode | null>(null)
  const busy = useRef(false)
  const query = useQuery({
    queryKey: key,
    queryFn: async (): Promise<TripsRead> => {
      const [open, newest] = await Promise.all([
        journalData('trips', { mode, status: 'open' }),
        readClosedPage(mode),
      ])
      // Over what the list holds when the answer is in, earlier pages included.
      return overNewest(queryClient.getQueryData<TripsRead>(key), open as TripRow[], newest)
    },
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const loadOlder = useCallback(async () => {
    const held = queryClient.getQueryData<TripsRead>(key)
    const to = held ? dayBefore(held) : null
    if (to === null || busy.current) return
    busy.current = true
    setLoading(mode)
    setPaging(null)
    try {
      const page = await readClosedPage(mode, to)
      queryClient.setQueryData<TripsRead>(key, (now) => (now ? withEarlier(now, page) : now))
    } catch (error) {
      setPaging({ mode, message: problemOf(error).message })
    } finally {
      busy.current = false
      setLoading(null)
    }
  }, [queryClient, key, mode])
  const data = query.data
  return {
    query,
    open: data?.open ?? [],
    closed: data?.closed ?? [],
    more: data?.more ?? false,
    partial: data?.partial ?? [],
    loadOlder,
    loadingOlder: loading === mode,
    pagingError: paging?.mode === mode ? paging.message : null,
  }
}

export function TripsList({ mode, onOpen }: { mode: Mode; onOpen: (id: number) => void }) {
  const access = usePriceAccess()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen, anyOpen } = useMarketOpen(market)
  const { query, open, closed, more, partial, loadOlder, loadingOlder, pagingError } =
    useTrips(mode)
  const tags = useQuery({
    queryKey: [...JOURNAL_KEY, 'tags'],
    queryFn: async () => journalData('tags_list'),
    retry: false,
    staleTime: 60_000,
  })
  const tagById = useMemo(() => new Map((tags.data ?? []).map((t) => [t.id, t])), [tags.data])
  const { refetch } = query
  const read = useCallback(() => refetch(), [refetch])
  useJournalSync(read, { marketOpen: anyOpen })
  const live = useTripLive({ trips: open, marketOpen: isOpen })
  const now = useNow()
  const days = useMemo(() => groupByDay(closed), [closed])

  let body: React.ReactNode
  if (query.isPending) {
    body = (
      <div className={cn(CARD, 'px-4 py-6 text-center')} aria-live="polite">
        <LoaderCircle aria-hidden="true" className="mx-auto mb-2 size-6 animate-spin" />
        <p className="text-base font-bold">Loading your trades</p>
      </div>
    )
  } else if (query.isError && !query.data) {
    body = (
      <div className={cn(CARD, 'px-4 py-6 text-center')} aria-live="polite">
        <TriangleAlert aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{problemOf(query.error).message}</p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="mt-3 inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  } else if (open.length === 0 && closed.length === 0) {
    body = (
      <div className={cn(CARD, 'px-4 py-6 text-center')}>
        <NotebookPen
          aria-hidden="true"
          className="mx-auto mb-2 size-6 text-[color:var(--text-secondary)]"
        />
        <p className="mx-auto max-w-sm text-sm font-medium text-[color:var(--text-secondary)]">
          {mode === 'sandbox'
            ? 'No Sandbox trades yet. Orders placed in Analyze mode are recorded here as they fill.'
            : access.state === 'upstox-logged-out'
              ? NO_TOKEN_WORDS
              : EMPTY_WORDS}
        </p>
      </div>
    )
  } else {
    body = (
      <>
        {query.isError && (
          <p
            role="alert"
            className="rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
          >
            {problemOf(query.error).message} The trades below are from the last read.
          </p>
        )}
        {open.length > 0 && (
          <section aria-label="Open trades" className="flex flex-col gap-2">
            <div className="flex min-h-8 items-center justify-between gap-2 px-1">
              <h2 className="text-[0.9375rem] font-bold">Open</h2>
              <LiveLine line={live.line} now={live.now} />
            </div>
            <ul className="flex flex-col gap-2">
              {open.map((trip) => (
                <OpenCard
                  key={trip.id}
                  trip={trip}
                  live={live.pnl.get(trip.id)}
                  now={now}
                  onOpen={onOpen}
                />
              ))}
            </ul>
          </section>
        )}
        {days.length > 0 && (
          <section aria-label="Closed trades" className="flex flex-col gap-3">
            <h2 className="px-1 text-[0.9375rem] font-bold">Closed</h2>
            {days.map((group) => (
              <div key={group.day} className="flex flex-col gap-2" data-testid="closed-day">
                <DayHeader group={group} partial={partial.includes(group.day)} />
                <ul className="flex flex-col gap-2">
                  {group.trips.map((trip) => (
                    <ClosedCard
                      key={trip.id}
                      trip={trip}
                      tags={(trip.mistakes ?? []).flatMap((id) => tagById.get(id) ?? [])}
                      onOpen={onOpen}
                    />
                  ))}
                </ul>
              </div>
            ))}
            {pagingError && (
              <p
                role="alert"
                className="px-1 text-sm font-medium text-[color:var(--text-secondary)]"
              >
                {pagingError}
              </p>
            )}
            {more && (
              <button
                type="button"
                disabled={loadingOlder}
                onClick={() => void loadOlder()}
                className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent disabled:opacity-60"
              >
                {loadingOlder && (
                  <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
                )}
                {loadingOlder ? 'Reading earlier trades' : 'Show earlier trades'}
              </button>
            )}
          </section>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <MarketCalendarSource key={day} onChange={setMarket} />
      {body}
    </div>
  )
}
