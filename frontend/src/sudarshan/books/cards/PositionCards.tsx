import { useQuery } from '@tanstack/react-query'
import { CandlestickChart, LogOut, Plus } from 'lucide-react'
import { memo, type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { cn } from '@/lib/utils'
import { useExitOrderReads } from '@/sudarshan/books/exitOrders'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { isFinalStatus } from '@/sudarshan/books/orderFold'
import type { TickDirection } from '@/sudarshan/books/prices'
import {
  exitMark,
  exitOrderStatuses,
  isHeld,
  marksOf,
  noteExitBook,
  type SendTarget,
  type SentMark,
  sendHeld,
  sentMarksNow,
  useSentMarks,
} from '@/sudarshan/books/sent'
import { istTime } from '@/sudarshan/books/time'
import { SymbolChartLink } from '@/sudarshan/chart/SymbolChartLink'
import { directionOf, formatRupees } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { FeedLine, MarketOpen } from '@/sudarshan/dashboard/live'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import {
  PRODUCT_MEANING,
  type Product,
  productsFor,
  ticketProduct,
} from '@/sudarshan/ticket/productPref'
import { contractOf } from '@/sudarshan/watchlist/api'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import type { Position } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { figure, sideOf, signedUnits, units } from './format'
import {
  ACTION,
  BookHeading,
  BUY_FILL,
  CARD,
  Confirm,
  Details,
  EmptyCard,
  Last,
  LoadingCards,
  Money,
  OUTLINED,
  PositionSide,
  ProblemCard,
  ProductChip,
  price,
  SANDBOX_SENTENCE,
  SELL_FILL,
  SentChip,
  SentNote,
  SignedPercent,
  STALE,
  StaleBookNote,
  useSandbox,
  WarningLine,
} from './parts'
import { useBookOverlay } from './useBookOverlay'
import { bookRow, type PricedRow, useFramePrices } from './useFramePrices'
import { useHeld, useLatest } from './useLatest'

/**
 * The Positions page on a phone (M6.9): the day's P&L at the top, one card per
 * open position, the closed ones below, and a sheet per position with Exit,
 * Add, its chart and the broker's details. Everything that sends an order is
 * the page's own: Exit is upstream's close-position call and Exit all its
 * close-all, each after a confirmation; Add opens the app's order ticket.
 */

/** A position as the book sends it, with the details Upstox and the sandbox add. */
export type BookPosition = Position & {
  realised?: unknown
  unrealised?: unknown
  unrealized_pnl?: unknown
  day_buy_quantity?: unknown
  day_buy_value?: unknown
  day_sell_quantity?: unknown
  day_sell_value?: unknown
  overnight_quantity?: unknown
}

export interface PositionCardsProps {
  /** The book as read, every position: the page's filters are not the phone's. */
  positions: Position[]
  /** When the book was asked for, so only newer ticks move it. */
  bookAt: number | null
  loading: boolean
  error: string | null
  refreshing: boolean
  apiKey: string | null
  marketOpen: MarketOpen
  /** The page's own read: `true` shows it refreshing. */
  onRefresh: (showRefresh?: boolean) => unknown
  /** The page's close-position call, the one its table's button makes. */
  onExit: (position: Position) => unknown
  /** The page's close-all call. */
  onExitAll: () => unknown
}

/** A position's place in the address: one per instrument and product. */
export function positionId(position: Pick<Position, 'exchange' | 'symbol' | 'product'>): string {
  return `${position.exchange}:${position.symbol}:${position.product}`
}

function quantityOf(position: Position): number {
  return Number(position.quantity) || 0
}

/** What an exit sent for this position holds: the position, and its size then. */
function exitTarget(position: Position): SendTarget {
  return {
    id: positionId(position),
    was: quantityOf(position),
    exchange: position.exchange,
    symbol: position.symbol,
    product: position.product,
  }
}

/**
 * An exit held by its own order, still working: what its card and its sheet's
 * Exit say. Where it wraps, it wraps at the dot ("check Orders" stays whole).
 */
export const EXIT_ORDER_OPEN = 'Exit order open\u00a0· check\u00a0Orders'

/** How an exit still held reads: on its card, in its note, and on its sheet's Exit. */
export function exitWords(
  mark: SentMark | undefined
): { chip: string | null; note: string | null; button: string } | null {
  if (!isHeld(mark)) return null
  if (mark.state === 'sending') return { chip: 'Sending exit', note: null, button: 'Sending exit' }
  // Its time is up, and only its own order, still working, holds it.
  if (mark.byOrder) {
    return { chip: EXIT_ORDER_OPEN, note: null, button: EXIT_ORDER_OPEN }
  }
  if (mark.state === 'unconfirmed') {
    return {
      chip: null,
      note: 'Not confirmed: check Orders before sending again',
      button: 'Not confirmed',
    }
  }
  const was = Math.abs(mark.was ?? 0)
  const left = Math.abs(mark.seen ?? was)
  const words =
    left > 0 && left < was ? `Exit sent · ${units(left)} of ${units(was)} left` : 'Exit sent'
  return { chip: words, note: null, button: words }
}

/**
 * What a confirmation says first when an earlier exit's time ran out with the
 * position still open: what became of that exit, as far as the book and its
 * own orders (pushed or read) say, and what this one would do.
 */
export function exitWarning(mark: SentMark | undefined): string | null {
  if (!mark?.expired) return null
  const at = istTime(mark.sentAt)
  const was = Math.abs(mark.was ?? 0)
  const left = Math.abs(mark.seen ?? was)
  const closed = left > 0 && left < was ? `${units(was - left)} of ${units(was)}` : null
  const statuses = exitOrderStatuses(mark)
  const ended = statuses.find((status) => status === 'rejected' || status === 'cancelled')
  const working = statuses.some((status) => !isFinalStatus(status))
  if (ended && !working) {
    return closed
      ? `The exit sent at ${at} closed ${closed}, and Orders shows the rest ${ended}. This sends an exit for the ${units(left)} left.`
      : `Orders shows the exit sent at ${at} ${ended}: it closed nothing. This sends a new exit.`
  }
  if (closed) {
    return `The exit sent at ${at} has closed ${closed} so far. If the rest is still open in Orders, this sends a second order for it.`
  }
  if (statuses.includes('complete') && !working) {
    return `Orders shows the exit sent at ${at} filled, but the book still shows this position. Check Positions first: a second exit would turn it the other way.`
  }
  const earlier =
    mark.state === 'unconfirmed'
      ? `The exit sent at ${at} was not confirmed and has not shown in the book yet.`
      : `The exit sent at ${at} has not shown in the book yet.`
  return `${earlier} If that order is still open in Orders, this sends a second one.`
}

/** Why a tap on Exit sends nothing: its position's exit is still held. */
export function heldWhy(mark: SentMark): string {
  const name = describe(mark.symbol, mark.exchange).title
  const at = istTime(mark.sentAt)
  if (mark.state === 'sending') {
    return `An exit for ${name} is still being sent. Wait for its answer before sending another.`
  }
  const was = Math.abs(mark.was ?? 0)
  const left = Math.abs(mark.seen ?? was)
  let where = 'has not shown in the book yet'
  if (mark.byOrder) where = 'its order is still open'
  else if (left > 0 && left < was) where = `has closed ${units(was - left)} of ${units(was)} so far`
  else if (mark.state === 'unconfirmed') where = 'is not confirmed'
  return `An exit for ${name} was sent at ${at} and ${where}. Check Orders before sending another.`
}

/** P&L booked today: the broker's realised figure; all of it, for a closed position. */
export function realisedOf(position: BookPosition): number | null {
  const booked = figure(position.realised) ?? figure(position.today_realized_pnl)
  if (booked !== null) return booked
  return quantityOf(position) === 0 ? figure(position.pnl) : null
}

/**
 * P&L still open: the broker's unrealised figure moved by what the price has
 * done since the book was read (the move in the row's P&L), or, when the
 * broker gives none, the P&L less what was booked. Nothing for a closed one.
 */
export function unrealisedOf(row: PricedRow<BookPosition>): number | null {
  const position = row.item
  if (quantityOf(position) === 0) return 0
  const booked = figure(position.pnl)
  const moved = row.pnl !== null && booked !== null ? row.pnl - booked : 0
  const open = figure(position.unrealised) ?? figure(position.unrealized_pnl)
  if (open !== null) return open + moved
  const realised = realisedOf(position)
  return realised !== null && row.pnl !== null ? row.pnl - realised : null
}

/** Rs 1: the broker's own rounding, not a disagreement. */
const SPLIT_TOLERANCE = 1

/**
 * Whether a position's booked and open add up to its P&L, as the book read it.
 * Upstox documents realised and unrealised as the day's P&L and pnl as the
 * position's net, so on a position carried in from an earlier day they need
 * not: the split would then say something the total does not, and only the
 * total is shown. An open part worked out as the P&L less what was booked adds
 * up by itself.
 */
export function splitAddsUp(position: BookPosition): boolean {
  const pnl = figure(position.pnl)
  const booked = realisedOf(position)
  if (pnl === null || booked === null) return false
  const open = figure(position.unrealised) ?? figure(position.unrealized_pnl)
  if (open === null) return true
  return Math.abs(booked + open - pnl) <= SPLIT_TOLERANCE
}

/** The book's P&L split into booked and open, when every position says and adds up. */
export function splitOf(
  rows: PricedRow<BookPosition>[]
): { realised: number; unrealised: number } | null {
  let realised = 0
  let unrealised = 0
  for (const row of rows) {
    if (!splitAddsUp(row.item)) return null
    const booked = realisedOf(row.item)
    const open = unrealisedOf(row)
    if (booked === null || open === null) return null
    realised += booked
    unrealised += open
  }
  return { realised, unrealised }
}

/** The product Add opens the ticket with: the position's own, so the order adds to it. */
export function addProduct(position: Position): Product | undefined {
  const product = position.product as Product
  return productsFor(position.exchange).includes(product)
    ? product
    : ticketProduct(position.exchange)
}

/** The contract's lot and tick size, read once and shared with the watchlist's sheet. */
function useContract(apiKey: string | null, symbol: string, exchange: string) {
  return useQuery({
    queryKey: ['sudarshan', 'contract', exchange, symbol],
    queryFn: () => contractOf(apiKey ?? '', symbol, exchange),
    enabled: apiKey !== null,
    staleTime: 6 * 60 * 60_000,
    retry: false,
  })
}

function feedRowOf(row: PricedRow<Position>): FeedRow {
  return {
    exchange: row.item.exchange,
    ltp: row.ltp ?? undefined,
    quantity: row.item.quantity,
    _at: row.at,
    _live: row.live,
  }
}

function openCount(count: number): string {
  if (count === 0) return 'No open positions'
  return count === 1 ? '1 open position' : `${count} open positions`
}

export function PositionCards(props: PositionCardsProps) {
  const onRefresh = useLatest(props.onRefresh)
  const onExit = useLatest(props.onExit)
  const onExitAll = useLatest(props.onExitAll)
  return <PositionBoard {...props} onRefresh={onRefresh} onExit={onExit} onExitAll={onExitAll} />
}

const PositionBoard = memo(function PositionBoard({
  positions,
  bookAt,
  loading,
  error,
  refreshing,
  apiKey,
  marketOpen,
  onRefresh,
  onExit,
  onExitAll,
}: PositionCardsProps) {
  // Only what is held is priced tick by tick, and so subscribed: a closed
  // position's figures are the book's own and never move.
  const book = positions as BookPosition[]
  const { rows: held, feed } = useFramePrices({
    items: book.filter((position) => quantityOf(position) !== 0),
    bookAt,
    marketOpen,
  })
  const closed = book
    .filter((position) => quantityOf(position) === 0)
    .map((position) => bookRow(position, bookAt))
  const rows = [...held, ...closed]
  const { line, now, stale } = useBookFeed(rows.map(feedRowOf), feed, marketOpen)
  const { overlay, open, swap, close } = useBookOverlay()
  const openSheet = useCallback((id: string) => open({ act: 'sheet', row: id }), [open])
  const refresh = useCallback(() => void onRefresh(true), [onRefresh])
  // Analyze mode: the book is the sandbox's, and so is every exit from it.
  const sandbox = useSandbox()

  // An exit sent holds the position's Exit (and Exit all) app-wide until the
  // book shows it closed (books/sent.ts): the broker's book can lag the order,
  // and a second exit sized from it would reverse the position. The page tells
  // the hold every book it reads (usePositionExits).
  const marks = useSentMarks()
  const exits = marksOf(marks, 'exit', sandbox)

  const total = rows.reduce((sum, row) => sum + (row.pnl ?? 0), 0)
  const figuresStale = (line.kind === 'delayed' || line.kind === 'connecting') && line.stale
  const target =
    overlay?.row != null ? (rows.find((row) => positionId(row.item) === overlay.row) ?? null) : null
  const targetOpen = target !== null && quantityOf(target.item) !== 0

  // Something open over a position the book no longer has (a mode switch, a
  // stale link), or an exit for one already closed, is closed once the book is known.
  const orphan =
    overlay !== null &&
    !loading &&
    (overlay.act === 'exitall'
      ? false
      : target === null || ((overlay.act === 'exit' || overlay.act === 'add') && !targetOpen))
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  let content: ReactNode
  if (positions.length === 0 && loading) {
    content = <LoadingCards />
  } else if (positions.length === 0 && error) {
    content = <ProblemCard message="Positions could not be read just now." onRetry={refresh} />
  } else if (positions.length === 0) {
    content = (
      <EmptyCard
        title="No open positions"
        hint="Positions you take today show here, moving with every tick."
      />
    )
  } else {
    content = (
      <>
        <Summary
          total={total}
          split={splitOf(rows)}
          open={held.length}
          exitsSent={exits.some(isHeld)}
          stale={figuresStale}
          line={line}
          now={now}
          onExitAll={() => open({ act: 'exitall', row: null })}
        />
        {error && <StaleBookNote text="Could not refresh just now. Showing the last figures." />}
        {held.length > 0 ? (
          <ul aria-label="Open positions" className="flex flex-col gap-2">
            {held.map((row) => {
              const words = exitWords(exitMark(marks, sandbox, positionId(row.item)))
              return (
                <PositionCard
                  key={positionId(row.item)}
                  id={positionId(row.item)}
                  symbol={row.item.symbol}
                  exchange={row.item.exchange}
                  product={row.item.product}
                  quantity={quantityOf(row.item)}
                  average={figure(row.item.average_price)}
                  ltp={row.ltp}
                  tick={row.tick}
                  pnl={row.pnl}
                  pnlPercent={row.pnlPercent}
                  stale={stale(feedRowOf(row))}
                  sentChip={words?.chip ?? null}
                  sentNote={words?.note ?? null}
                  onOpen={openSheet}
                />
              )
            })}
          </ul>
        ) : (
          <EmptyCard title="No open positions" hint="Everything taken today is closed." />
        )}
        {closed.length > 0 && (
          <section aria-labelledby="sd-closed-positions" className="flex flex-col gap-2 pt-1">
            <h2
              id="sd-closed-positions"
              className="px-1 text-sm font-bold text-[color:var(--text-secondary)]"
            >
              Closed ({closed.length})
            </h2>
            <ul aria-label="Closed positions" className="flex flex-col gap-2">
              {closed.map((row) => (
                <ClosedCard
                  key={positionId(row.item)}
                  id={positionId(row.item)}
                  symbol={row.item.symbol}
                  exchange={row.item.exchange}
                  product={row.item.product}
                  pnl={row.pnl}
                  onOpen={openSheet}
                />
              ))}
            </ul>
          </section>
        )}
      </>
    )
  }

  const exiting = overlay?.act === 'exit' && targetOpen ? target : null
  const exitShown = useHeld(exiting)
  const exitName = exitShown ? describe(exitShown.item.symbol, exitShown.item.exchange).title : ''
  const exitEarlier = exitWarning(
    exitShown ? exitMark(marks, sandbox, positionId(exitShown.item)) : undefined
  )
  // Exit all after an exit whose time ran out unseen says so, with the latest.
  const allEarlier = exitWarning(
    exits
      .filter((mark) => mark.expired && held.some((row) => positionId(row.item) === mark.id))
      .sort((a, b) => b.sentAt - a.sentAt)[0]
  )

  return (
    <div className="flex flex-col gap-3">
      <BookHeading title="Positions" refreshing={refreshing} onRefresh={refresh} />
      {content}

      {overlay?.act === 'sheet' && target && (
        <PositionSheet
          row={target}
          stale={stale(feedRowOf(target))}
          line={line}
          now={now}
          apiKey={apiKey}
          held={exitWords(exitMark(marks, sandbox, positionId(target.item)))?.button ?? null}
          sandbox={sandbox}
          onClose={close}
          onExit={() => swap({ act: 'exit', row: positionId(target.item) })}
          onAdd={() => swap({ act: 'add', row: positionId(target.item) })}
        />
      )}

      <Confirm
        open={exiting !== null}
        title={`Exit ${exitName}?`}
        description={
          exitShown ? (
            <>
              {exitEarlier && <WarningLine text={exitEarlier} />}
              {exitSentence(exitShown.item, sandbox)}
            </>
          ) : (
            ''
          )
        }
        cancelLabel="Keep it"
        confirmLabel="Exit"
        confirmClassName={exitShown && quantityOf(exitShown.item) < 0 ? BUY_FILL : SELL_FILL}
        onConfirm={() => {
          if (!exiting) return
          const { item } = exiting
          const mark = exitMark(sentMarksNow(), sandbox, positionId(item))
          // Held since this opened (the book showed the earlier exit working again):
          // nothing is sent, and the toast says why.
          if (isHeld(mark)) return void showToast.warning(heldWhy(mark), 'positions')
          void sendHeld('exit', sandbox, [exitTarget(item)], () => onExit(item))
        }}
        onClose={close}
      />

      <Confirm
        open={overlay?.act === 'exitall'}
        title="Exit all positions?"
        description={
          <>
            {allEarlier && <WarningLine text={allEarlier} />}
            {`This closes all ${held.length} open ${held.length === 1 ? 'position' : 'positions'} at market price${sandbox ? ` in the sandbox. ${SANDBOX_SENTENCE}` : '. This cannot be undone.'}`}
          </>
        }
        confirmLabel="Exit all"
        onConfirm={() => {
          const first = marksOf(sentMarksNow(), 'exit', sandbox).find(isHeld)
          if (first) return void showToast.warning(heldWhy(first), 'positions')
          void sendHeld(
            'exit',
            sandbox,
            held.map((row) => exitTarget(row.item)),
            () => onExitAll()
          )
        }}
        onClose={close}
      />

      {overlay?.act === 'add' && targetOpen && target && (
        <AddTicket position={target.item} apiKey={apiKey} onClose={close} />
      )}
    </div>
  )
})

/** The day's P&L in one big figure, what is booked and what is open, and Exit all. */
function Summary({
  total,
  split,
  open,
  exitsSent,
  stale,
  line,
  now,
  onExitAll,
}: {
  total: number
  split: { realised: number; unrealised: number } | null
  open: number
  /** An exit went and the book has not caught up yet: Exit all would size from it too. */
  exitsSent: boolean
  stale: boolean
  line: FeedLine
  now: number
  onExitAll: () => void
}) {
  const direction = directionOf(total)
  return (
    <section
      aria-labelledby="sd-positions-total"
      className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex min-h-5 items-center justify-between gap-3">
        <h2
          id="sd-positions-total"
          className="text-[0.84375rem] font-semibold text-muted-foreground"
        >
          Total P&amp;L
        </h2>
        <LiveLine line={line} now={now} />
      </div>
      <p
        data-testid="positions-total"
        data-stale={stale || undefined}
        className={cn('flex items-center gap-2', stale ? STALE : TREND_TEXT[direction])}
      >
        <TrendArrow direction={direction} className="size-[0.9375rem]" />
        <span className="sd-display text-[2.625rem] leading-[1.05] font-bold">
          {formatRupees(total, { signed: true })}
        </span>
        {stale && <span className="sr-only">Stale</span>}
      </p>
      {split && (
        <p className="flex flex-wrap gap-x-3.5 gap-y-0.5 text-[0.8125rem] text-muted-foreground">
          <span>
            Realised <Money value={split.realised} className="font-bold" />
          </span>
          <span>
            Unrealised <Money value={split.unrealised} stale={stale} className="font-bold" />
          </span>
        </p>
      )}
      <div className="mt-1 flex items-center justify-between gap-3">
        <p className="text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
          {openCount(open)}
        </p>
        <button
          type="button"
          disabled={open === 0 || exitsSent}
          onClick={onExitAll}
          aria-haspopup="dialog"
          className={cn(OUTLINED, 'border-destructive text-destructive')}
        >
          <LogOut aria-hidden="true" className="size-4" />
          Exit all
        </button>
      </div>
    </section>
  )
}

/** One open position: redrawn only when its own figures change. */
const PositionCard = memo(function PositionCard({
  id,
  symbol,
  exchange,
  product,
  quantity,
  average,
  ltp,
  tick,
  pnl,
  pnlPercent,
  stale,
  sentChip,
  sentNote,
  onOpen,
}: {
  id: string
  symbol: string
  exchange: string
  product: string
  quantity: number
  average: number | null
  ltp: number | null
  tick: TickDirection | null
  pnl: number | null
  pnlPercent: number | null
  stale: boolean
  /** Its exit is held: "Exit sent", "Exit sent · 65 of 130 left". */
  sentChip: string | null
  /** Its exit got no answer: what to do before sending another. */
  sentNote: string | null
  onOpen: (id: string) => void
}) {
  const named = describe(symbol, exchange)
  return (
    <li data-stale={stale || undefined} data-position={id}>
      <button type="button" aria-haspopup="dialog" onClick={() => onOpen(id)} className={CARD}>
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <ProductChip product={product} />
            <PositionSide quantity={quantity} />
            {sentChip && <SentChip label={sentChip} />}
          </span>
          <Last value={ltp} tick={tick} stale={stale} exchange={exchange} className="text-sm" />
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span
            className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
            title={symbol}
          >
            {named.title}
          </span>
          <Money value={pnl} stale={stale} className="text-base leading-tight font-bold" />
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            {named.detail} · Avg {price(average, exchange)}
          </span>
          <SignedPercent value={pnlPercent} stale={stale} className="text-xs font-semibold" />
        </span>
        {sentNote && <SentNote text={sentNote} />}
      </button>
    </li>
  )
})

/** A position closed today: muted, still showing what it booked. */
const ClosedCard = memo(function ClosedCard({
  id,
  symbol,
  exchange,
  product,
  pnl,
  onOpen,
}: {
  id: string
  symbol: string
  exchange: string
  product: string
  pnl: number | null
  onOpen: (id: string) => void
}) {
  const named = describe(symbol, exchange)
  return (
    <li data-position={id} data-closed="true">
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onOpen(id)}
        className={cn(CARD, 'bg-[color:var(--surface-2)] shadow-none')}
      >
        <span className="flex items-baseline justify-between gap-3">
          <span
            className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold text-[color:var(--text-secondary)]"
            title={symbol}
          >
            {named.title}
          </span>
          <Money value={pnl} className="text-[0.9375rem] leading-tight font-bold" />
        </span>
        <span className="flex items-baseline justify-between gap-3 text-xs font-medium text-[color:var(--text-tertiary)]">
          <span className="min-w-0 truncate">
            {named.detail} · {product} · Closed
          </span>
          <span>Booked</span>
        </span>
      </button>
    </li>
  )
})

/** One position's sheet: its P&L, Exit and Add, its chart, and what the broker says about it. */
function PositionSheet({
  row,
  stale,
  line,
  now,
  apiKey,
  held,
  sandbox,
  onClose,
  onExit,
  onAdd,
}: {
  row: PricedRow<BookPosition>
  stale: boolean
  line: FeedLine
  now: number
  apiKey: string | null
  /** Its exit is held, and Exit waits, saying why: "Exit sent", "Not confirmed". */
  held: string | null
  /** Analyze mode: Exit and Add go to the sandbox, and say so. */
  sandbox: boolean
  onClose: () => void
  onExit: () => void
  onAdd: () => void
}) {
  const position = row.item
  const { symbol, exchange, product } = position
  const named = describe(symbol, exchange)
  const quantity = quantityOf(position)
  const side = sideOf(quantity)
  const contract = useContract(side ? apiKey : null, symbol, exchange)
  // Booked and open apart only when they add up to the P&L at the top (see splitAddsUp).
  const split = splitAddsUp(position)
  const realised = split ? realisedOf(position) : null
  const unrealised = split ? unrealisedOf(row) : null
  const dayBuy = [figure(position.day_buy_quantity), figure(position.day_buy_value)] as const
  const daySell = [figure(position.day_sell_quantity), figure(position.day_sell_value)] as const
  const overnight = figure(position.overnight_quantity)
  const traded = (pair: readonly [number | null, number | null]) =>
    `${pair[0] === null ? '-' : units(pair[0])} · ${pair[1] === null ? '-' : formatRupees(pair[1])}`

  return (
    <BottomSheet
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={named.title}
      description={named.detail}
      aside={
        <div className="flex flex-col items-end gap-0.5 pt-2">
          <Money
            value={row.pnl}
            stale={side !== null && stale}
            className="sd-display text-[1.5rem] leading-none font-bold"
            arrowClassName="size-3"
          />
          {side && (
            <SignedPercent value={row.pnlPercent} stale={stale} className="text-sm font-semibold" />
          )}
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <ProductChip product={product} />
            <PositionSide quantity={quantity} />
          </span>
          {side && <LiveLine line={line} now={now} />}
        </div>

        {side ? (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                aria-haspopup="dialog"
                disabled={held !== null}
                onClick={onExit}
                className={cn(
                  ACTION,
                  'flex-col gap-0 py-1.5',
                  side === 'LONG' ? SELL_FILL : BUY_FILL
                )}
              >
                <span className="flex items-center gap-1.5 font-bold">
                  <LogOut aria-hidden="true" className="size-4" />
                  Exit
                </span>
                <span className="text-[0.71875rem] font-semibold">
                  {held ??
                    `${side === 'LONG' ? 'Sell' : 'Buy'} ${units(Math.abs(quantity))} ${sandbox ? 'in the sandbox' : 'at market'}`}
                </span>
              </button>
              <button
                type="button"
                aria-haspopup="dialog"
                disabled={!contract.data}
                onClick={onAdd}
                className={cn(
                  ACTION,
                  'flex-col gap-0 py-1.5',
                  side === 'LONG' ? BUY_FILL : SELL_FILL
                )}
              >
                <span className="flex items-center gap-1.5 font-bold">
                  <Plus aria-hidden="true" className="size-4" />
                  Add
                </span>
                <span className="text-[0.71875rem] font-semibold">
                  {side === 'LONG' ? 'Buy more' : 'Sell more'}
                  {sandbox ? ' in the sandbox' : ''}
                </span>
              </button>
            </div>
            {contract.isPending && apiKey !== null && (
              <p className="text-xs font-medium text-muted-foreground">Reading the lot size.</p>
            )}
            {contract.isError && (
              <p className="flex flex-wrap items-center gap-x-2 text-xs font-medium text-[color:var(--warning-text)]">
                The lot size could not be read, so the order ticket stays shut.
                <button
                  type="button"
                  onClick={() => void contract.refetch()}
                  className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
                >
                  Try again
                </button>
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm font-medium text-muted-foreground">
            Closed. The P&amp;L above is what it booked.
          </p>
        )}

        <SymbolChartLink symbol={symbol} exchange={exchange} className={cn(OUTLINED, 'w-full')}>
          <CandlestickChart aria-hidden="true" className="size-5" />
          Chart
        </SymbolChartLink>

        <Details
          rows={[
            side !== null && {
              label: 'Average price',
              value: price(figure(position.average_price), exchange),
            },
            side !== null && {
              label: 'Last price',
              value: (
                <Last
                  value={row.ltp}
                  tick={row.tick}
                  stale={stale}
                  exchange={exchange}
                  label={null}
                />
              ),
            },
            realised !== null && { label: 'Realised', value: <Money value={realised} /> },
            side !== null &&
              unrealised !== null && {
                label: 'Unrealised',
                value: <Money value={unrealised} stale={stale} />,
              },
            (dayBuy[0] !== null || dayBuy[1] !== null) && {
              label: 'Bought today',
              value: traded(dayBuy),
            },
            (daySell[0] !== null || daySell[1] !== null) && {
              label: 'Sold today',
              value: traded(daySell),
            },
            overnight !== null && { label: 'Overnight quantity', value: signedUnits(overnight) },
            {
              label: 'Product',
              value: PRODUCT_MEANING[product as Product]
                ? `${product} · ${PRODUCT_MEANING[product as Product]}`
                : product,
            },
          ]}
        />
      </div>
    </BottomSheet>
  )
}

/**
 * Add: the app's order ticket on the position's side and product, one lot to
 * start with. It opens only once the lot size is known: a derivative ticket
 * counts in lots, and a guessed lot of one would misstate every quantity.
 */
function AddTicket({
  position,
  apiKey,
  onClose,
}: {
  position: Position
  apiKey: string | null
  onClose: () => void
}) {
  const contract = useContract(apiKey, position.symbol, position.exchange)
  if (!contract.data) return null
  return (
    <PlaceOrderDialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      symbol={position.symbol}
      exchange={position.exchange}
      action={quantityOf(position) < 0 ? 'SELL' : 'BUY'}
      lotSize={contract.data.lotSize}
      tickSize={contract.data.tickSize}
      product={addProduct(position)}
      strategy="Positions"
    />
  )
}

/** What an exit sends, as its confirmation says it. */
function exitSentence(position: Position, sandbox: boolean): string {
  const quantity = quantityOf(position)
  return `${sideOf(quantity) === 'SHORT' ? 'Buys' : 'Sells'} ${units(Math.abs(quantity))} (${position.product}) at market price${sandbox ? ' in the sandbox' : ''} to close this position. ${sandbox ? SANDBOX_SENTENCE : 'This cannot be undone.'}`
}

/**
 * The Positions page's side of the exit hold (M6.9), on a phone and on a wide
 * screen alike: every book the page reads is told to the hold, an exit waiting
 * on its own order has the order book read for it (exitOrders.ts), and the
 * wide table's Close and Close All are held as the phone's Exit is (a phone
 * turned on its side shows the table). A tap on a held one sends nothing and
 * says why; Close offered again after an earlier exit's time ran out asks
 * first, with the same warning the phone's confirmation shows, and so does
 * Close All (`allWarning`, for the page's own confirmation). The page renders
 * `dialog`.
 */
export function usePositionExits({
  onClose,
  onCloseAll,
  positions,
  bookAt,
  loading,
  apiKey,
}: {
  onClose: (position: Position) => unknown
  onCloseAll: () => unknown
  positions: Position[]
  /** When the shown book was asked for. */
  bookAt: number | null
  loading: boolean
  apiKey: string | null
}): {
  close: (position: Position) => void
  closeAll: () => void
  allWarning: string | null
  dialog: ReactNode
} {
  const sandbox = useSandbox()
  const marks = useSentMarks()
  // The position Close asks about first, by its place: the book can change under the dialog.
  const [asking, setAsking] = useState<string | null>(null)

  // Each book read tells the exits sent in the book it was read from (a mode
  // switch alone changes nothing until the other book has been read).
  const bookMode = useRef({ at: bookAt, sandbox })
  useEffect(() => {
    if (bookMode.current.at !== bookAt) bookMode.current = { at: bookAt, sandbox }
    if (loading || bookAt === null) return
    noteExitBook(
      bookMode.current.sandbox,
      positions.map((position) => ({ id: positionId(position), quantity: quantityOf(position) })),
      bookAt
    )
  }, [positions, bookAt, loading, sandbox])
  useExitOrderReads(sandbox, apiKey)

  const refuse = (mark: SentMark) => showToast.warning(heldWhy(mark), 'positions')
  const send = (position: Position) =>
    void sendHeld('exit', sandbox, [exitTarget(position)], () => onClose(position))
  const open = positions.filter((position) => quantityOf(position) !== 0)
  const allWarning = exitWarning(
    marksOf(marks, 'exit', sandbox)
      .filter((mark) => mark.expired && open.some((position) => positionId(position) === mark.id))
      .sort((a, b) => b.sentAt - a.sentAt)[0]
  )
  // Closed, or gone from the book, while it asked: nothing left to close.
  const askedPosition = open.find((position) => positionId(position) === asking) ?? null
  useEffect(() => {
    if (asking !== null && askedPosition === null && !loading) setAsking(null)
  }, [asking, askedPosition, loading])
  const asked = asking ? exitMark(marks, sandbox, asking) : undefined

  return {
    close: (position) => {
      const mark = exitMark(sentMarksNow(), sandbox, positionId(position))
      if (mark && !mark.expired) return refuse(mark)
      // An earlier exit's time ran out unseen: ask first, saying what is known of it.
      if (mark) return setAsking(positionId(position))
      send(position)
    },
    // Held while any exit is, as the phone's Exit all is: it sizes from the same book.
    closeAll: () => {
      const first = marksOf(sentMarksNow(), 'exit', sandbox).find(isHeld)
      if (first) return refuse(first)
      void sendHeld('exit', sandbox, open.map(exitTarget), () => onCloseAll())
    },
    allWarning,
    dialog: (
      <TableExitConfirm
        position={askedPosition}
        warning={exitWarning(asked)}
        sandbox={sandbox}
        onConfirm={(position) => {
          const mark = exitMark(sentMarksNow(), sandbox, positionId(position))
          if (isHeld(mark)) return refuse(mark)
          send(position)
        }}
        onClose={() => setAsking(null)}
      />
    ),
  }
}

/** The wide table's Close, asked first because an earlier exit's time ran out unseen. */
function TableExitConfirm({
  position,
  warning,
  sandbox,
  onConfirm,
  onClose,
}: {
  position: Position | null
  warning: string | null
  sandbox: boolean
  onConfirm: (position: Position) => void
  onClose: () => void
}) {
  const shown = useHeld(position)
  // Gone with the table (the phone turned upright): never reopened by a turn back.
  const close = useLatest(onClose)
  useEffect(() => () => close(), [close])
  return (
    <Confirm
      open={position !== null}
      title={`Close ${shown ? describe(shown.symbol, shown.exchange).title : ''}?`}
      description={
        shown ? (
          <>
            {warning && <WarningLine text={warning} />}
            {exitSentence(shown, sandbox)}
          </>
        ) : (
          ''
        )
      }
      cancelLabel="Keep it"
      confirmLabel="Close"
      confirmClassName={shown && quantityOf(shown) < 0 ? BUY_FILL : SELL_FILL}
      onConfirm={() => {
        if (position) onConfirm(position)
      }}
      onClose={onClose}
    />
  )
}
