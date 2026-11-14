// SUDARSHAN P6 D4 (M3.1, M6.7): the Holdings cards for a family viewer, the counterpart of
// ReadOnlyPositions. `HoldingCards` takes this path when it is given `readOnly`. No Buy more, no
// Sell, no order ticket, no chart link; a figure the member's mask removed shows as Hidden and is
// never worked out from what is left (a value without its quantity would be a guess). While P&L
// is hidden, the current value, the amount invested, the average and the day's change show as
// Hidden too, header and sheet alike: beside the quantity each gives the P&L away (review H1,
// `holdingFigures`).
import { useEffect, useMemo } from 'react'
import { cn } from '@/lib/utils'
import { formatRupees } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import {
  type HoldingFigures,
  holdingFigures,
  instrumentOf,
  type MaskedHolding,
  totalOf,
} from '@/sudarshan/family/books'
import { useViewerLive } from '@/sudarshan/family/useViewerLive'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import { units } from './format'
import { Hidden } from './Hidden'
import {
  CARD,
  Details,
  EmptyCard,
  Last,
  LoadingCards,
  ProblemCard,
  price,
  SignedPercent,
  STALE,
} from './parts'
import {
  Figure,
  NotRealTime,
  Plain,
  type ReadOnlyBookProps,
  ReadStamp,
  ServiceWords,
  UnreadCard,
  unreadWords,
  ViewerNote,
} from './readOnlyParts'
import { useBookOverlay } from './useBookOverlay'

export type ReadOnlyHoldingsProps = ReadOnlyBookProps<MaskedHolding>

const NOTE = "The account owner's holdings. Nothing here can be changed from this view."

const idOf = (row: MaskedHolding) => `${row.exchange}:${row.symbol ?? row.name}`
const keyOf = (row: MaskedHolding) => {
  const instrument = instrumentOf(row)
  return instrument ? `${instrument.exchange.toUpperCase()}:${instrument.symbol.toUpperCase()}` : ''
}

export function ReadOnlyHoldings({
  rows,
  readAt,
  state,
  words,
  loading,
  error,
  onRetry,
  marketOpen,
}: ReadOnlyHoldingsProps) {
  const instruments = useMemo(
    () => rows.map(instrumentOf).filter((i): i is NonNullable<typeof i> => i !== null),
    [rows]
  )
  const live = useViewerLive({ instruments, marketOpen })
  const { overlay, open, close } = useBookOverlay()

  const figured = useMemo(
    () =>
      rows.map((row) => ({
        row,
        id: idOf(row),
        instrument: instrumentOf(row),
        capped: live.capped.has(keyOf(row)),
        figures: holdingFigures(row, live.ticks.get(keyOf(row)), readAt),
      })),
    [rows, live.ticks, live.capped, readAt]
  )
  const current = totalOf(figured.map((entry) => entry.figures.value))
  const invested = totalOf(figured.map((entry) => entry.figures.invested))
  const pnl = totalOf(figured.map((entry) => entry.figures.pnl))
  const today = totalOf(figured.map((entry) => entry.figures.today))
  const percent =
    pnl === undefined
      ? undefined
      : pnl === null || typeof invested !== 'number' || invested <= 0
        ? null
        : (pnl / invested) * 100
  const stale = (live.line.kind === 'delayed' || live.line.kind === 'connecting') && live.line.stale

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
      <EmptyCard title="No holdings" hint={words ?? 'Shares held in the account show here.'} />
    )
  } else {
    content = (
      <>
        <section
          aria-labelledby="sd-family-holdings-value"
          className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
        >
          <div className="flex min-h-5 items-center justify-between gap-3">
            <h2
              id="sd-family-holdings-value"
              className="text-[0.84375rem] font-semibold text-muted-foreground"
            >
              Current value
            </h2>
            <LiveLine line={live.line} now={live.now} />
          </div>
          <p
            data-testid="holdings-value"
            data-stale={stale || undefined}
            className={cn('sd-display text-[2.375rem] leading-[1.05] font-bold', stale && STALE)}
          >
            <Plain
              value={current}
              className="sd-display text-[2.375rem] leading-[1.05] font-bold"
            />
            {stale && <span className="sr-only"> Stale</span>}
          </p>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-[color:var(--divider)] pt-2">
            <div className="flex min-w-0 flex-col gap-px">
              <dt className="text-xs font-semibold text-muted-foreground">Invested</dt>
              <dd className="text-[0.9375rem] font-bold">
                <Plain value={invested} />
              </dd>
            </div>
            <div className="flex min-w-0 flex-col gap-px">
              <dt className="text-xs font-semibold text-muted-foreground">Overall P&amp;L</dt>
              <dd className="flex flex-wrap items-baseline gap-x-1.5 text-[0.9375rem] font-bold">
                <Figure value={pnl} stale={stale} />
                {percent !== undefined && (
                  <SignedPercent value={percent} stale={stale} className="text-xs font-semibold" />
                )}
              </dd>
            </div>
            <div className="col-span-2 flex min-w-0 flex-col gap-px">
              <dt className="text-xs font-semibold text-muted-foreground">Day&apos;s change</dt>
              <dd className="flex flex-wrap items-baseline gap-x-1.5 text-[0.9375rem] font-bold">
                {today === null ? (
                  <span className="text-sm font-medium text-muted-foreground">
                    Waiting for the previous close
                  </span>
                ) : (
                  <Figure value={today} stale={stale} />
                )}
              </dd>
            </div>
          </dl>
        </section>
        <ServiceWords state={state} words={words} />
        {error && <ProblemCard message={error} onRetry={onRetry} />}
        <ul aria-label="Holdings" className="flex flex-col gap-2">
          {figured.map(({ row, id, instrument, capped, figures }) => (
            <HoldingCard
              key={id}
              row={row}
              id={id}
              figures={figures}
              stale={live.stale(instrument)}
              capped={capped}
              readAt={readAt}
              tick={live.directions.get(keyOf(row)) ?? null}
              onOpen={(next) => open({ act: 'sheet', row: next })}
            />
          ))}
        </ul>
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <ViewerNote name="holdings" text={NOTE} />
      {content}
      <ReadStamp part="Holdings" readAt={readAt} />
      {overlay?.act === 'sheet' && target && (
        <HoldingSheet
          row={target.row}
          figures={target.figures}
          stale={live.stale(target.instrument)}
          line={live.line}
          now={live.now}
          onClose={close}
        />
      )}
    </div>
  )
}

function HoldingCard({
  row,
  id,
  figures,
  stale,
  capped,
  readAt,
  tick,
  onOpen,
}: {
  row: MaskedHolding
  id: string
  figures: HoldingFigures
  stale: boolean
  capped: boolean
  readAt: number | null
  tick: 'up' | 'down' | null
  onOpen: (id: string) => void
}) {
  const exchange = row.exchange ?? ''
  const named = describe(row.symbol ?? row.name ?? '', exchange)
  return (
    <li data-stale={stale || undefined} data-holding={id}>
      <button type="button" aria-haspopup="dialog" onClick={() => onOpen(id)} className={CARD}>
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
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-1 text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            Qty {figures.quantity === undefined ? <Hidden /> : units(figures.quantity)} · Avg{' '}
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
        <span className="flex items-baseline justify-between gap-3 text-[0.8125rem] leading-tight">
          <span className="flex min-w-0 items-baseline gap-1.5">
            <Last value={figures.ltp} tick={tick} stale={stale} exchange={exchange} />
            {figures.dayPercent !== null && (
              <span className="whitespace-nowrap text-xs font-medium text-[color:var(--text-tertiary)]">
                Day{' '}
                <SignedPercent value={figures.dayPercent} stale={stale} className="font-semibold" />
              </span>
            )}
          </span>
          <span className="flex items-baseline gap-1 whitespace-nowrap text-xs font-medium text-[color:var(--text-tertiary)]">
            Value{' '}
            <Plain value={figures.value} stale={stale} className="font-semibold text-foreground" />
          </span>
        </span>
        {capped && <NotRealTime readAt={readAt} />}
      </button>
    </li>
  )
}

/** One holding's sheet: its figures and what the broker says about it, and no action. */
function HoldingSheet({
  row,
  figures,
  stale,
  line,
  now,
  onClose,
}: {
  row: MaskedHolding
  figures: HoldingFigures
  stale: boolean
  line: Parameters<typeof LiveLine>[0]['line']
  now: number
  onClose: () => void
}) {
  const exchange = row.exchange ?? ''
  const named = describe(row.symbol ?? row.name ?? '', exchange)
  const product = row.product ?? 'CNC'
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
            stale={stale}
            className="sd-display text-[1.5rem] leading-none font-bold"
          />
          {figures.pnlPercent !== undefined && (
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
        <div className="flex justify-end">
          <LiveLine line={line} now={now} />
        </div>
        <Details
          rows={[
            {
              label: 'Quantity',
              value: figures.quantity === undefined ? <Hidden /> : units(figures.quantity),
            },
            {
              label: 'Average price',
              value: figures.average === undefined ? <Hidden /> : price(figures.average, exchange),
            },
            {
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
            { label: 'Invested', value: <Plain value={figures.invested} /> },
            { label: 'Current value', value: <Plain value={figures.value} stale={stale} /> },
            { label: 'Overall P&L', value: <Figure value={figures.pnl} stale={stale} /> },
            { label: 'Day’s change', value: <Figure value={figures.today} stale={stale} /> },
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
