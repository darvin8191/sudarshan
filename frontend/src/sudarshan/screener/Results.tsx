import { ChevronDown, ChevronUp, Clock, Hourglass, RefreshCw, TriangleAlert } from 'lucide-react'
import { memo, type ReactNode, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { EmptyCard, LoadingCards, OUTLINED, STALE } from '@/sudarshan/books/cards/parts'
import { istTime } from '@/sudarshan/books/time'
import { formatPercent, formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { ago, keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import type { FeedState as LiveFeed } from '@/sudarshan/live/useFrameLines'
import { describe, priceDecimals } from '@/sudarshan/watchlist/rows'
import { type LiveRow, useLiveRows } from '@/sudarshan/watchlist/useLiveRows'
import { Figures, type FigureValues, figuresOf } from '@/sudarshan/watchlist/WatchlistRows'
import { ChipMark } from './ChipMark'
import { whenWords } from './cardLine'
import type { Hearing } from './push'
import { followedItems } from './resultItems'
import type { ResultRow, Results as ResultsData, ScreenRow } from './types'
import { WORDS } from './types'
import { useResults } from './useResults'
import { warmingNote } from './words'

/** What stands under the run when nothing in the screen is current. */
export const NOT_RECEIVING = 'Not receiving updates on this screen · reconnecting'

/** The run in words: 'Run at 10:30:05 · 10:15 to 10:30 candle'. */
export function runWords(run: NonNullable<ResultsData['run']>): string {
  return `Run at ${istTime(run.at)} · ${run.candle} candle`
}

/** The live stream's name for a row: EXCHANGE:SYMBOL (the service's own `key` names the feed's instrument). */
const KEY = (row: Pick<ResultRow, 'symbol' | 'exchange'>) => keyOf(row.symbol, row.exchange)

/** A row's clause values and its "forming" mark. */
export function Values({ row }: { row: ResultRow }) {
  if (!row.values && !row.forming) return null
  return (
    <span className="flex flex-wrap items-center gap-x-2 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
      {row.values && <span>{row.values}</span>}
      {row.forming && (
        <span className="inline-flex items-center gap-1 font-semibold text-[color:var(--warning-text)]">
          <Hourglass aria-hidden="true" className="size-3.5" />
          forming
        </span>
      )}
    </span>
  )
}

/** A row past the live cap: the price of the run, grey, and where it comes from. */
export function RunFigures({ row, at }: { row: ResultRow; at: number }) {
  return (
    <span className={cn('flex shrink-0 flex-col items-end gap-0.5 text-right', STALE)}>
      <span className="text-base font-bold">
        {row.ltp !== null ? formatPrice(row.ltp, priceDecimals(row.exchange)) : '—'}
      </span>
      <span className="flex items-center gap-1 text-xs font-semibold">
        {row.change_pct !== null && formatPercent(row.change_pct)}
        <Clock aria-hidden="true" className="size-3" />
        at the run, {whenWords(at, Date.now())}
      </span>
    </span>
  )
}

/**
 * What a row draws from the live stream, as plain figures: a row redraws only when one of them
 * changes, so a tick frame redraws the rows that ticked and no other (the live hook answers new
 * objects for every row every frame, which would defeat `memo` if the row took them whole).
 */
export interface RowLive {
  hasLive: boolean
  ltp: number | null
  change: number | null
  changePercent: number | null
  direction: FigureValues['direction']
  stale: boolean
  /** When the last tick came, only while the row is stale: it names the age. */
  staleAt: number | null
}

export const NO_LIVE: RowLive = {
  hasLive: false,
  ltp: null,
  change: null,
  changePercent: null,
  direction: null,
  stale: false,
  staleAt: null,
}

export function rowLiveOf(live: LiveRow | undefined): RowLive {
  // A row with no price yet shows the price at the run, dated, rather than a dash.
  if (!live || live.view.ltp === null) return NO_LIVE
  const figures = figuresOf(live)
  return {
    hasLive: true,
    ltp: figures.ltp,
    change: figures.change,
    changePercent: figures.changePercent,
    direction: figures.direction,
    stale: figures.stale,
    staleAt: live.stale ? live.view.at : null,
  }
}

/** The clock a row is given: only a stale row's age moves with it, so the others never redraw for it. */
export const clockFor = (live: RowLive, now: number) => (live.staleAt !== null ? now : 0)

export const sameRowLive = (a: RowLive, b: RowLive) =>
  a.hasLive === b.hasLive &&
  a.ltp === b.ltp &&
  a.change === b.change &&
  a.changePercent === b.changePercent &&
  a.direction === b.direction &&
  a.stale === b.stale &&
  a.staleAt === b.staleAt

/** One row of a screen's matches (also the dashboard card's row): 56 dp, one button. */
export const ResultLine = memo(
  ResultFace,
  (a, b) =>
    a.row === b.row &&
    a.runAt === b.runAt &&
    a.now === b.now &&
    a.onOpen === b.onOpen &&
    sameRowLive(a.live, b.live)
)

function ResultFace({
  row,
  live,
  runAt,
  now,
  onOpen,
}: {
  row: ResultRow
  live: RowLive
  runAt: number
  /** The clock, given only while the row is stale (its age moves each second); 0 otherwise. */
  now: number
  onOpen: (key: string) => void
}) {
  const named = describe(row.symbol, row.exchange)
  const key = KEY(row)
  const stale = live.stale
  return (
    <li data-stale={stale || undefined} data-result={key}>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onOpen(key)}
        className="flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-2">
            <span
              className="truncate text-[0.9375rem] leading-tight font-semibold"
              title={row.symbol}
            >
              {named.title}
            </span>
            {row.chip && <ChipMark chip={row.chip} />}
          </span>
          <span className="truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            {named.detail}
          </span>
          <Values row={row} />
          {stale && live.staleAt !== null && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--warning-text)]">
              <Clock aria-hidden="true" className="size-3" />
              Stale · updated {ago(now - live.staleAt)}
            </span>
          )}
        </span>
        {live.hasLive ? (
          <Figures
            ltp={live.ltp}
            change={live.change}
            changePercent={live.changePercent}
            direction={live.direction}
            stale={live.stale}
            decimals={priceDecimals(row.exchange)}
          />
        ) : (
          <RunFigures row={row} at={runAt} />
        )}
      </button>
    </li>
  )
}

/** "Not checked (12)": the reasons the screen could not judge some instruments, in the service's words. */
export function NotChecked({ data }: { data: ResultsData['not_checked'] }) {
  const [open, setOpen] = useState(false)
  if (data.count === 0) return null
  const Icon = open ? ChevronUp : ChevronDown
  return (
    <section className="rounded-[1rem] border bg-card shadow-[var(--shadow-card)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((on) => !on)}
        className="flex min-h-12 w-full items-center justify-between gap-2 px-4 text-left text-sm font-bold outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="inline-flex items-center gap-1.5 text-[color:var(--warning-text)]">
          <TriangleAlert aria-hidden="true" className="size-4" />
          Not checked ({data.count})
        </span>
        <Icon aria-hidden="true" className="size-5" />
      </button>
      {open && (
        <ul className="flex flex-col gap-1 border-t px-4 py-2 text-[0.8125rem] font-medium">
          {data.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
          {warmingNote(data.reasons) && (
            <li className="flex items-start gap-1.5 font-semibold text-[color:var(--text-secondary)]">
              <Hourglass aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {warmingNote(data.reasons)}
            </li>
          )}
        </ul>
      )}
    </section>
  )
}

/**
 * A screen's matches (design 4.1, 5.3): the latest run's rows, ranked, each with its price and %
 * change from the page's own tick stream (at most 100 tick by tick), the membership chip, at most
 * two clause values and "forming" for a value read from a forming candle. Matches change only at a
 * run; a pushed run patches the list at once (push.ts). The price line says "Real-time" only while
 * ticks arrive; a row whose ticks stopped greys out with "Stale · updated 12s ago". When this
 * screen is not in the owner's room, the list says it is not being kept current and "as of the
 * run" is all it claims.
 */
export function Results({
  screen,
  apiKey,
  isOpen,
  hearing,
  openKey,
  onOpen,
  children,
}: {
  screen: ScreenRow
  apiKey: string | null
  isOpen: MarketOpen
  hearing: Hearing
  /** The row whose sheet is open, EXCHANGE:SYMBOL, or null. */
  openKey: string | null
  onOpen: (key: string) => void
  /** The symbol sheet, given the open row and what the live stream knows (the page owns the ticket). */
  children?: (open: { row: LiveRow | null; feed: LiveFeed; now: number }) => ReactNode
}) {
  const query = useResults(screen.id)
  const data = query.data
  const rows = data?.rows ?? NO_RESULT_ROWS
  const items = useMemo(() => followedItems(rows), [rows])
  const live = useLiveRows({ items, apiKey, marketOpen: isOpen })
  const liveByKey = useMemo(() => new Map(live.rows.map((row) => [row.key, row])), [live.rows])
  const sheetRow = openKey === null ? null : (liveByKey.get(openKey) ?? null)
  const notCurrent = hearing === 'not_joined'

  if (!data) {
    if (query.isPending) return <LoadingCards />
    return (
      <section
        role="alert"
        className="flex flex-col items-start gap-3 rounded-[1.125rem] border border-destructive bg-card px-4 py-3.5"
      >
        <p className="flex items-start gap-2 text-sm font-semibold">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          {query.error?.message ?? WORDS.unreadable}
        </p>
        <button type="button" onClick={() => void query.refetch()} className={OUTLINED}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </section>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5 px-1">
        {data.run ? (
          <p
            data-run={data.run.id}
            className="text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]"
          >
            {runWords(data.run)} · {data.run.matches} match
          </p>
        ) : (
          <p className="text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            {screen.status === 'active' ? 'Not checked yet.' : 'Not checking.'}{' '}
            {screen.next_check ?? ''}
          </p>
        )}
        {notCurrent && (
          <p
            data-results="not-current"
            className="inline-flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
          >
            <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {NOT_RECEIVING}
          </p>
        )}
        {apiKey !== null && live.line && rows.length > 0 && (
          <LiveLine line={live.line} now={live.now} className="flex min-h-5" />
        )}
      </div>

      {rows.length === 0 ? (
        <EmptyCard
          title={data.run ? 'Nothing matches at the last check' : 'No results yet'}
          hint={
            data.run
              ? 'The list fills as instruments start to match.'
              : 'Results appear after the first check of this screen.'
          }
        />
      ) : (
        <section
          aria-label="Matches"
          className="overflow-hidden rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]"
        >
          <ul className="divide-y">
            {rows.map((row) => {
              const own = rowLiveOf(liveByKey.get(KEY(row)))
              return (
                <ResultLine
                  key={row.key ?? KEY(row)}
                  row={row}
                  live={own}
                  runAt={data.run?.at ?? 0}
                  now={clockFor(own, live.now)}
                  onOpen={onOpen}
                />
              )
            })}
          </ul>
        </section>
      )}
      <NotChecked data={data.not_checked} />
      {children?.({ row: sheetRow, feed: live.feed, now: live.now })}
    </div>
  )
}

const NO_RESULT_ROWS: ResultRow[] = []
