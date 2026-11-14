// SUDARSHAN P6 D4 (M3.1, M6.7): the Positions cards for a family viewer. `PositionCards` takes
// this path when it is given `readOnly`. It is a board of its own, not the owner's with parts
// switched off, because the owner's board reads owner endpoints (the kill switch, sent exits,
// the sandbox, the access check) that a viewer phone must never reach. What it shares with the
// owner's board is the look: the same parts, the same words for a live price, the same sheet.
//
// What it has not got: Exit, Exit all, Add, the kill switch, held exits, the order ticket and
// the chart link (which leads to an owner page). Nothing here sends anything.
import { useEffect, useMemo } from 'react'
import { cn } from '@/lib/utils'
import { directionOf, formatRupees } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import {
  instrumentOf,
  isOpen,
  type MaskedPosition,
  type PositionFigures,
  positionFigures,
  sideOf,
  totalOf,
} from '@/sudarshan/family/books'
import { useViewerLive } from '@/sudarshan/family/useViewerLive'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import { signedUnits } from './format'
import { Hidden } from './Hidden'
import {
  CARD,
  Details,
  EmptyCard,
  Last,
  LoadingCards,
  Money,
  ProblemCard,
  ProductChip,
  price,
  SignedPercent,
  STALE,
} from './parts'
import {
  Figure,
  HiddenTotal,
  NotRealTime,
  type ReadOnlyBookProps,
  ReadStamp,
  ServiceWords,
  UnreadCard,
  unreadWords,
  ViewerNote,
} from './readOnlyParts'
import { useBookOverlay } from './useBookOverlay'

export type ReadOnlyPositionsProps = ReadOnlyBookProps<MaskedPosition>

const NOTE = "The account owner's positions. Nothing here can be changed from this view."

const idOf = (row: MaskedPosition) => `${row.exchange}:${row.symbol ?? row.name}:${row.product}`

function openCount(count: number): string {
  if (count === 0) return 'No open positions'
  return count === 1 ? '1 open position' : `${count} open positions`
}

/** LONG or SHORT in words, with the signed quantity beside it where the member may see it. */
function Direction({ figures }: { figures: PositionFigures }) {
  const { side, quantity } = figures
  if (side === null) return null
  if (side === 'closed') {
    return <span className="text-[0.8125rem] font-semibold text-muted-foreground">Closed</span>
  }
  const word = side === 'long' ? 'LONG' : 'SHORT'
  return (
    <span
      data-side={word}
      className={cn(
        'inline-flex items-center gap-1 text-[0.8125rem] font-bold whitespace-nowrap',
        side === 'long' ? 'text-[color:var(--buy-solid)]' : 'text-[color:var(--sell-solid)]'
      )}
    >
      {quantity !== undefined && signedUnits(quantity)}
      <span className="text-[0.71875rem] tracking-wide">{word}</span>
    </span>
  )
}

export function ReadOnlyPositions({
  rows,
  readAt,
  hidden,
  state,
  words,
  loading,
  error,
  onRetry,
  marketOpen,
}: ReadOnlyPositionsProps) {
  const open = useMemo(() => rows.filter(isOpen), [rows])
  const instruments = useMemo(
    () => open.map(instrumentOf).filter((i): i is NonNullable<typeof i> => i !== null),
    [open]
  )
  const live = useViewerLive({ instruments, marketOpen })
  const { overlay, open: openSheet, close } = useBookOverlay()

  const figured = useMemo(
    () =>
      rows.map((row) => {
        const instrument = instrumentOf(row)
        const key = instrument
          ? `${instrument.exchange.toUpperCase()}:${instrument.symbol.toUpperCase()}`
          : ''
        return {
          row,
          id: idOf(row),
          instrument,
          capped: live.capped.has(key),
          figures: positionFigures(row, live.ticks.get(key), readAt),
        }
      }),
    [rows, live.ticks, live.capped, readAt]
  )
  const held = figured.filter((entry) => isOpen(entry.row))
  const closed = figured.filter((entry) => !isOpen(entry.row))
  const total = totalOf(figured.map((entry) => entry.figures.pnl))
  const longs = held.filter((entry) => sideOf(entry.row) === 'long').length
  const shorts = held.filter((entry) => sideOf(entry.row) === 'short').length
  const figuresStale =
    (live.line.kind === 'delayed' || live.line.kind === 'connecting') && live.line.stale

  // A sheet opens from a tap or an address. Any step an owner's page takes from the address
  // (an exit, an add, a kill switch) means nothing here and is closed at once.
  const target =
    overlay?.row != null ? (figured.find((entry) => entry.id === overlay.row) ?? null) : null
  const orphan = overlay !== null && (overlay.act !== 'sheet' || (!loading && target === null))
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  const unread = unreadWords(state, words, rows.length, readAt)
  let content
  if (rows.length === 0 && loading) {
    content = <LoadingCards />
  } else if (rows.length === 0 && error) {
    content = <ProblemCard message={error} onRetry={onRetry} />
  } else if (unread !== null) {
    // not an empty account: it was not read (review M4)
    content = <UnreadCard text={unread} />
  } else if (rows.length === 0) {
    content = (
      <EmptyCard
        title="No open positions"
        hint={words ?? 'Positions taken today show here, moving with every tick.'}
      />
    )
  } else {
    content = (
      <>
        <section
          aria-labelledby="sd-family-positions-total"
          className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
        >
          <div className="flex min-h-5 items-center justify-between gap-3">
            <h2
              id="sd-family-positions-total"
              className="text-[0.84375rem] font-semibold text-muted-foreground"
            >
              Total P&amp;L
            </h2>
            <LiveLine line={live.line} now={live.now} />
          </div>
          {total === undefined ? (
            <p data-testid="positions-total" className="flex items-center gap-2 py-1">
              <HiddenTotal />
            </p>
          ) : total === null ? (
            <p data-testid="positions-total" className="py-1">
              <Money value={null} className="text-lg font-bold" />
            </p>
          ) : (
            <p
              data-testid="positions-total"
              data-stale={figuresStale || undefined}
              className={cn(
                'flex items-center gap-2',
                figuresStale ? STALE : TREND_TEXT[directionOf(total)]
              )}
            >
              <TrendArrow direction={directionOf(total)} className="size-[0.9375rem]" />
              <span className="sd-display text-[2.625rem] leading-[1.05] font-bold">
                {formatRupees(total, { signed: true })}
              </span>
              {figuresStale && <span className="sr-only">Stale</span>}
            </p>
          )}
          <p className="mt-1 text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
            {openCount(held.length)}
            {held.length > 0 && ` · ${longs} long · ${shorts} short`}
          </p>
        </section>
        <ServiceWords state={state} words={words} />
        {error && <ProblemCard message={error} onRetry={onRetry} />}
        {held.length > 0 ? (
          <ul aria-label="Open positions" className="flex flex-col gap-2">
            {held.map(({ row, id, instrument, capped, figures }) => (
              <PositionCard
                key={id}
                row={row}
                id={id}
                figures={figures}
                stale={live.stale(instrument)}
                capped={capped}
                readAt={readAt}
                tick={
                  instrument
                    ? (live.directions.get(
                        `${instrument.exchange.toUpperCase()}:${instrument.symbol.toUpperCase()}`
                      ) ?? null)
                    : null
                }
                onOpen={(next) => openSheet({ act: 'sheet', row: next })}
              />
            ))}
          </ul>
        ) : (
          <EmptyCard title="No open positions" hint="Everything taken today is closed." />
        )}
        {closed.length > 0 && (
          <section aria-labelledby="sd-family-closed" className="flex flex-col gap-2 pt-1">
            <h2
              id="sd-family-closed"
              className="px-1 text-sm font-bold text-[color:var(--text-secondary)]"
            >
              Closed ({closed.length})
            </h2>
            <ul aria-label="Closed positions" className="flex flex-col gap-2">
              {closed.map(({ row, id, figures }) => (
                <ClosedCard
                  key={id}
                  row={row}
                  id={id}
                  figures={figures}
                  onOpen={(next) => openSheet({ act: 'sheet', row: next })}
                />
              ))}
            </ul>
          </section>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <ViewerNote name="positions" text={NOTE} />
      {content}
      <ReadStamp
        part="Positions"
        readAt={readAt}
        closed={state === 'closed' || marketOpen('') === false}
      />
      {overlay?.act === 'sheet' && target && (
        <PositionSheet
          row={target.row}
          figures={target.figures}
          stale={live.stale(target.instrument)}
          line={live.line}
          now={live.now}
          hidden={hidden}
          onClose={close}
        />
      )}
    </div>
  )
}

function PositionCard({
  row,
  id,
  figures,
  stale,
  capped,
  readAt,
  tick,
  onOpen,
}: {
  row: MaskedPosition
  id: string
  figures: PositionFigures
  stale: boolean
  capped: boolean
  readAt: number | null
  tick: 'up' | 'down' | null
  onOpen: (id: string) => void
}) {
  const named = describe(row.symbol ?? row.name ?? '', row.exchange ?? '')
  const exchange = row.exchange ?? ''
  return (
    <li data-stale={stale || undefined} data-position={id}>
      <button type="button" aria-haspopup="dialog" onClick={() => onOpen(id)} className={CARD}>
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <ProductChip product={row.product} />
            <Direction figures={figures} />
          </span>
          <span className="flex items-center gap-2">
            <Last
              value={figures.ltp}
              tick={tick}
              stale={stale}
              exchange={exchange}
              className="text-sm"
            />
            <SignedPercent
              value={figures.dayPercent}
              stale={stale}
              className="text-xs font-semibold"
            />
          </span>
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span
            className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
            title={row.symbol ?? ''}
          >
            {named.title}
          </span>
          <Figure value={figures.pnl} stale={stale} className="text-base leading-tight font-bold" />
        </span>
        <span className="flex items-baseline justify-between gap-3">
          <span className="flex min-w-0 items-baseline gap-1 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            {named.detail} · Avg{' '}
            {figures.average === undefined ? <Hidden /> : price(figures.average, exchange)}
          </span>
          {figures.pnlPercent !== undefined && (
            <SignedPercent
              value={figures.pnlPercent}
              stale={stale}
              className="text-xs font-semibold"
            />
          )}
        </span>
        {capped && <NotRealTime readAt={readAt} />}
      </button>
    </li>
  )
}

function ClosedCard({
  row,
  id,
  figures,
  onOpen,
}: {
  row: MaskedPosition
  id: string
  figures: PositionFigures
  onOpen: (id: string) => void
}) {
  const named = describe(row.symbol ?? row.name ?? '', row.exchange ?? '')
  return (
    <li data-position={id} data-closed="true">
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onOpen(id)}
        className={cn(CARD, 'bg-[color:var(--surface-2)] shadow-none')}
      >
        <span className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold text-[color:var(--text-secondary)]">
            {named.title}
          </span>
          <Figure value={figures.pnl} className="text-[0.9375rem] leading-tight font-bold" />
        </span>
        <span className="flex items-baseline justify-between gap-3 text-xs font-medium text-[color:var(--text-tertiary)]">
          <span className="min-w-0 truncate">
            {named.detail} · {row.product} · Closed
          </span>
          <span>Booked</span>
        </span>
      </button>
    </li>
  )
}

/** One position's sheet: its figures and what the broker says about it, and no action. */
function PositionSheet({
  row,
  figures,
  stale,
  line,
  now,
  onClose,
}: {
  row: MaskedPosition
  figures: PositionFigures
  stale: boolean
  line: Parameters<typeof LiveLine>[0]['line']
  now: number
  hidden: readonly string[]
  onClose: () => void
}) {
  const exchange = row.exchange ?? ''
  const named = describe(row.symbol ?? row.name ?? '', exchange)
  const open = isOpen(row)
  const traded = (qtyU: number | undefined) =>
    qtyU === undefined ? <Hidden /> : signedUnits(qtyU / 1_000_000)
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
          <Figure
            value={figures.pnl}
            stale={open && stale}
            className="sd-display text-[1.5rem] leading-none font-bold"
          />
          {figures.pnlPercent !== undefined && open && (
            <SignedPercent
              value={figures.pnlPercent}
              stale={stale}
              className="text-sm font-semibold"
            />
          )}
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <ProductChip product={row.product} />
            <Direction figures={figures} />
          </span>
          {open && <LiveLine line={line} now={now} />}
        </div>
        {!open && (
          <p className="text-sm font-medium text-muted-foreground">
            Closed. The P&amp;L above is what it booked.
          </p>
        )}
        <Details
          rows={[
            open && {
              label: 'Average price',
              value: figures.average === undefined ? <Hidden /> : price(figures.average, exchange),
            },
            open && {
              label: 'Last price',
              value: (
                <Last
                  value={figures.ltp}
                  tick={null}
                  stale={stale}
                  exchange={exchange}
                  label={null}
                />
              ),
            },
            { label: 'Realised', value: <Figure value={figures.realised} /> },
            open && {
              label: 'Unrealised',
              value: <Figure value={figures.unrealised} stale={stale} />,
            },
            { label: 'Bought today', value: traded(row.day_buy_qty_u) },
            { label: 'Sold today', value: traded(row.day_sell_qty_u) },
            {
              label: 'Overnight quantity',
              value: traded(row.overnight_qty_u),
            },
            {
              label: 'Product',
              value: PRODUCT_MEANING[row.product as Product]
                ? `${row.product} · ${PRODUCT_MEANING[row.product as Product]}`
                : row.product,
            },
          ]}
        />
      </div>
    </BottomSheet>
  )
}
