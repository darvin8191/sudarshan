// SUDARSHAN M14, JD2: the Trades part. Open trades first with their P&L moving with every tick,
// then the positions not in the figures (opened before the journal started, or ended at expiry),
// then closed trades by day, newest first. The service's list is the first snapshot and a re-sync
// (useJournalSync); between reads the figures of an open trade move only with ticks.
import { useQuery } from '@tanstack/react-query'
import {
  CalendarX,
  Clock,
  History,
  LoaderCircle,
  NotebookPen,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
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
import { journalData, problemOf } from './api'
import { JOURNAL_KEY } from './keys'
import {
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
import { TagIcons } from './tags'
import type { Mode, Tag, Trip } from './types'
import { useJournalSync } from './useJournalSync'
import { type LivePnl, useTripLive } from './useTripLive'

/** A page of closed trades. */
export const CLOSED_PAGE = 100
/** The most of each kind of position not in the figures one read lists. */
export const ASIDE_PAGE = 50

export const ASIDE_WORDS =
  'The journal has no result for these: it did not see them opened, or they ended at expiry with no fill. They are not in your figures; Upstox’s own report has them.'

export const EMPTY_WORDS = 'No trades yet. Fills are recorded here as they happen at Upstox.'
export const NO_TOKEN_WORDS =
  'Fills are recorded when Upstox reports them; sign in to Upstox for today’s to show at once. The day’s close check, after the market closes, records them in any case.'

/** A trade as the list reads it: the contract's row, and the tags the service may name on it. */
export type TripRow = Trip & { mistakes?: number[] }

interface TripsRead {
  open: TripRow[]
  closed: TripRow[]
  /** Opened before the journal started, or ended at expiry: never in a figure. */
  aside: TripRow[]
  /** Whether a full page of closed trades came back: there may be earlier ones. */
  more: boolean
}

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
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-base font-bold">{trip.symbol}</span>
            <SideChip side={trip.side} />
          </span>
          {live?.pnl_paise !== null && live?.pnl_paise !== undefined ? (
            <span className="text-base font-bold" data-testid="live-pnl">
              <Signed paise={live.pnl_paise} stale={live.stale} />
              <OutcomeWord paise={live.pnl_paise} />
            </span>
          ) : (
            <span className="text-sm font-semibold text-[color:var(--text-tertiary)]">
              Waiting for a price
            </span>
          )}
        </span>
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

/** One position the journal pairs no result for: what it is, in words and an icon, never a
 * LONG or SHORT for one it did not see opened, and no figure. */
function AsideCard({ trip, onOpen }: { trip: TripRow; onOpen: (id: number) => void }) {
  const unseen = trip.status === 'unmatched'
  const closedOut = trip.exit_qty
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(trip.id)}
        data-testid="aside-trip"
        className={cn(
          CARD,
          'flex min-h-12 w-full flex-col gap-1.5 px-4 py-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
        )}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-base font-bold">{trip.symbol}</span>
            {!unseen && <SideChip side={trip.side} />}
          </span>
          <span className="inline-flex shrink-0 items-center gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            {unseen ? (
              <History aria-hidden="true" className="size-3.5" />
            ) : (
              <CalendarX aria-hidden="true" className="size-3.5" />
            )}
            {unseen ? 'Opened before the journal' : 'Ended at expiry'}
          </span>
        </span>
        <span className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {unseen
            ? `${trip.side === 'long' ? 'Sold' : 'Bought back'} ${closedOut} qty; the price it was opened at is not known here`
            : `${openQty(trip)} qty at ${priceText(trip.entry_avg_u)}${
                trip.close_day !== null ? `, expired ${dayWords(trip.close_day)}` : ''
              }`}
        </span>
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
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-base font-bold">{trip.symbol}</span>
            <SideChip side={trip.side} />
          </span>
          {net === null ? (
            <span className="text-sm font-semibold text-[color:var(--text-tertiary)]">
              Net not worked out yet
            </span>
          ) : (
            <span className="inline-flex items-baseline gap-1.5 text-base font-bold">
              <Signed paise={net} />
              <OutcomeWord paise={net} show={net === 0} />
              {trip.charges_actual_paise === null && (
                <span className="text-[0.6875rem] font-semibold text-[color:var(--text-tertiary)]">
                  {trip.charges_modelled === false ? 'no charges counted' : 'est.'}
                </span>
              )}
            </span>
          )}
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {trip.hold_s !== null && (
            <span className="inline-flex items-center gap-1">
              <Clock aria-hidden="true" className="size-3.5" />
              Held {holdWords(trip.hold_s * 1000)}
            </span>
          )}
          <StrategyChip name={strategy} />
          <ExcursionBar trip={trip} />
          <TagIcons tags={tags} />
        </span>
      </button>
    </li>
  )
}

/** Positions not in the figures, newest first: by the day each ended, else the day it opened. */
export function asideOrder(trips: TripRow[]): TripRow[] {
  const day = (t: TripRow) => t.close_day ?? t.open_day
  return [...trips].sort((a, b) => day(b) - day(a) || b.id - a.id)
}

/** The trades of one mode: open and closed, read from the journal. */
export function useTrips(mode: Mode) {
  const [older, setOlder] = useState<TripRow[]>([])
  const [more, setMore] = useState<boolean | null>(null)
  const [paging, setPaging] = useState<string | null>(null)
  useEffect(() => {
    // Another mode never mixes with this one: nothing read for one is kept for the other.
    void mode
    setOlder([])
    setMore(null)
    setPaging(null)
  }, [mode])
  const query = useQuery({
    queryKey: [...JOURNAL_KEY, 'trips', mode],
    queryFn: async (): Promise<TripsRead> => {
      const [open, closed, unmatched, expired] = await Promise.all([
        journalData('trips', { mode, status: 'open' }),
        journalData('trips', { mode, status: 'closed', limit: CLOSED_PAGE }),
        journalData('trips', { mode, status: 'unmatched', limit: ASIDE_PAGE }),
        journalData('trips', { mode, status: 'expired', limit: ASIDE_PAGE }),
      ])
      return {
        open: open as TripRow[],
        closed: closed as TripRow[],
        aside: asideOrder([...(unmatched as TripRow[]), ...(expired as TripRow[])]),
        more: closed.length >= CLOSED_PAGE,
      }
    },
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const data = query.data
  const loadOlder = useCallback(async () => {
    const shown = [...(data?.closed ?? []), ...older]
    const last = shown.at(-1)
    if (!last || last.closed_ms === null) return
    setPaging(null)
    try {
      // The service pages closed trades by their close time, strictly before `before`, and
      // several can close in the same second: asking up to and including the last one's time,
      // and leaving out what is already shown, loses none that share it.
      const page = (await journalData('trips', {
        mode,
        status: 'closed',
        limit: CLOSED_PAGE,
        before: last.closed_ms + 1,
      })) as TripRow[]
      const fresh = page.filter((t) => !shown.some((n) => n.id === t.id))
      setOlder((now) => [...now, ...fresh.filter((t) => !now.some((n) => n.id === t.id))])
      setMore(page.length >= CLOSED_PAGE && fresh.length > 0)
    } catch (error) {
      setPaging(problemOf(error).message)
    }
  }, [data, older, mode])
  const closed = useMemo(() => {
    const first = data?.closed ?? []
    return [...first, ...older.filter((t) => !first.some((f) => f.id === t.id))]
  }, [data, older])
  return {
    query,
    open: data?.open ?? [],
    aside: data?.aside ?? [],
    closed,
    more: more ?? data?.more ?? false,
    loadOlder,
    pagingError: paging,
  }
}

export function TripsList({ mode, onOpen }: { mode: Mode; onOpen: (id: number) => void }) {
  const access = usePriceAccess()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen, anyOpen } = useMarketOpen(market)
  const { query, open, aside, closed, more, loadOlder, pagingError } = useTrips(mode)
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
  } else if (open.length === 0 && closed.length === 0 && aside.length === 0) {
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
        {aside.length > 0 && (
          <section aria-label="Not in the figures" className="flex flex-col gap-2">
            <div className="flex flex-col gap-0.5 px-1">
              <h2 className="text-[0.9375rem] font-bold">Not in the figures</h2>
              <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
                {ASIDE_WORDS}
              </p>
            </div>
            <ul className="flex flex-col gap-2">
              {aside.map((trip) => (
                <AsideCard key={trip.id} trip={trip} onOpen={onOpen} />
              ))}
            </ul>
          </section>
        )}
        {days.length > 0 && (
          <section aria-label="Closed trades" className="flex flex-col gap-3">
            <h2 className="px-1 text-[0.9375rem] font-bold">Closed</h2>
            {days.map((group) => (
              <div key={group.day} className="flex flex-col gap-2" data-testid="closed-day">
                <div className="flex items-baseline justify-between gap-2 px-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
                  <span>{dayWords(group.day)}</span>
                  <span className="inline-flex items-baseline gap-2">
                    {group.trips.length} {group.trips.length === 1 ? 'trade' : 'trades'}
                    {group.net_paise !== null && <Signed paise={group.net_paise} />}
                  </span>
                </div>
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
                onClick={() => void loadOlder()}
                className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
              >
                Show earlier trades
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
