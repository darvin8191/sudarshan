/**
 * The labels of the phone chart's lines (M4.7, C5), drawn over the chart where
 * each line stands: a position's side, size and average with its P&L moving on
 * every tick; a working order's side, size, type and price; an active alert's
 * level. Each kind has its own icon and its line its own dash, and BUY and SELL
 * are always written, so nothing is told by colour alone.
 *
 * The engine draws the lines and their price tags; this draws the words, as
 * real elements, so a screen reader reads them and a finger can find them. The
 * host says where each line is after every paint, so a label follows its line
 * through a pan, a pinch and a rescale.
 *
 * With `onOpen` a label is a 48 px touch target that opens the line's sheet
 * (C6). Lines too close for a target each share one, which opens a list.
 */
import { AlertTriangle, Bell, Briefcase } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { positionPnlAt } from '@/hooks/useLivePrice'
import type { SymbolData } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { Money } from '@/sudarshan/books/cards/parts'
import { priceRow } from '@/sudarshan/books/prices'
import type { Position } from '@/types/trading'
import { OVER_CHART } from './longPress'
import { type ChartLine, clusterLabels, type LabelCluster } from './overlays'
import type { PhoneChart } from './phoneChart'
import type { PriceStore } from './prices'

/** A label's height. */
const PILL = 20

/**
 * A position's P&L as the Positions screen works it out (`priceRow`): the
 * broker's own figure, moved by what the price has done since the book was
 * read. Only a tick newer than the book moves it.
 */
export function positionPnl(
  position: Position,
  bookAt: number | null,
  tick: { ltp: number; at: number; polled: boolean } | null
): number | null {
  const line = tick
    ? ({
        symbol: position.symbol,
        exchange: position.exchange,
        data: { ltp: tick.ltp },
        lastUpdate: tick.at,
        updateSource: tick.polled ? 'rest' : 'websocket',
      } as SymbolData)
    : undefined
  const { pnl } = priceRow(position, { tick: line, bookAt }, positionPnlAt)
  return typeof pnl === 'number' && Number.isFinite(pnl) ? pnl : null
}

function LivePnl({
  position,
  bookAt,
  prices,
  quiet,
}: {
  position: Position
  bookAt: number | null
  prices: PriceStore
  quiet: boolean
}) {
  const { tick } = useSyncExternalStore(prices.subscribe, prices.get)
  return (
    <Money
      value={positionPnl(position, bookAt, tick)}
      stale={quiet}
      className="font-bold tabular-nums"
      arrowClassName="size-2"
    />
  )
}

function Icon({ line }: { line: ChartLine }) {
  if (line.stale) return <AlertTriangle aria-hidden="true" className="size-3 shrink-0" />
  if (line.kind === 'position') return <Briefcase aria-hidden="true" className="size-3 shrink-0" />
  if (line.kind === 'alert') return <Bell aria-hidden="true" className="size-3 shrink-0" />
  return (
    <span
      aria-hidden="true"
      className="inline-flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border border-current text-[0.5625rem] leading-none font-bold"
    >
      {line.side === 'SELL' ? 'S' : 'B'}
    </span>
  )
}

const INK: Record<string, string> = {
  BUY: 'border-[color:var(--buy-solid)] text-[color:var(--buy-solid)]',
  SELL: 'border-[color:var(--sell-solid)] text-[color:var(--sell-solid)]',
  none: 'border-[color:var(--info)] text-[color:var(--info)]',
  stale: 'border-[color:var(--stale)] text-[color:var(--stale)]',
}

function Pill({
  line,
  extra,
  top,
  bookAt,
  prices,
  quiet,
}: {
  line: ChartLine
  extra: number
  /** Its place in a target it shares; null where it has the target to itself. */
  top: number | null
  bookAt: number | null
  prices: PriceStore
  quiet: boolean
}) {
  return (
    <span
      className={cn(
        // Never cut short: the price is the last thing in it.
        'left-1.5 flex shrink-0 items-center gap-1 rounded-full border bg-card/90 px-1.5 text-[0.6875rem] leading-none font-semibold whitespace-nowrap',
        top === null ? 'relative' : 'absolute',
        INK[line.stale ? 'stale' : (line.side ?? 'none')]
      )}
      style={{ top: top ?? undefined, height: PILL }}
      data-line={line.id}
      data-stale={line.stale || undefined}
    >
      <Icon line={line} />
      <span className="text-[color:var(--text-primary)]">{line.label}</span>
      {line.ref.kind === 'position' && (
        <LivePnl
          position={line.ref.position}
          bookAt={bookAt}
          prices={prices}
          quiet={quiet || line.stale === true}
        />
      )}
      {line.note && <span className="font-medium">{line.note}</span>}
      {extra > 0 && (
        <span className="font-bold">
          +{extra}
          <span className="sr-only"> more here</span>
        </span>
      )}
    </span>
  )
}

export function LineLabels({
  owner,
  lines,
  more,
  bookAt,
  prices,
  quiet,
  onOpen,
}: {
  owner: PhoneChart
  lines: readonly ChartLine[]
  /** What the cap of twelve left out, in words. */
  more: string | null
  bookAt: number | null
  prices: PriceStore
  /** The price is not real-time just now, so a P&L is drawn grey. */
  quiet: boolean
  /** A tap on a label: the ids of the lines under it. Left out, labels are only read. */
  onOpen?: (ids: string[]) => void
}) {
  const ys = useSyncExternalStore(owner.onOverlayLayout, owner.overlayLayout)
  if (!lines.length) return null
  const byId = new Map(lines.map((line) => [line.id, line]))
  const clusters = clusterLabels(ys.filter((y) => byId.has(y.id)))
  const body = (cluster: LabelCluster) =>
    cluster.shown.map((shown) => {
      const line = byId.get(shown.id)
      return line ? (
        <Pill
          key={shown.id}
          line={line}
          extra={shown.extra}
          top={cluster.shown.length === 1 ? null : shown.y - cluster.top - PILL / 2}
          bookAt={bookAt}
          prices={prices}
          quiet={quiet}
        />
      ) : null
    })
  return (
    <div className="pointer-events-none absolute inset-0 z-[3]" data-testid="chart-lines">
      {clusters.map((cluster) => {
        const style = { top: cluster.top, height: cluster.height }
        const key = cluster.ids[0]
        if (!onOpen)
          return (
            <div key={key} className="absolute right-0 left-0 flex items-center" style={style}>
              {body(cluster)}
            </div>
          )
        const alone = cluster.shown.length === 1
        return (
          <button
            key={key}
            type="button"
            aria-haspopup="dialog"
            onClick={() => onOpen(cluster.ids)}
            // As wide as its one label, so the chart beside it still pans; a shared one is wider.
            className={cn(
              'pointer-events-auto absolute left-0 min-w-12 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              alone ? 'flex items-center pr-1.5' : 'w-[min(100%,17rem)]'
            )}
            style={style}
            {...{ [OVER_CHART]: '' }}
          >
            {body(cluster)}
            <span className="sr-only">
              {cluster.ids.length === 1
                ? '. Open its actions'
                : `. ${cluster.ids.length} lines here: choose one`}
            </span>
          </button>
        )
      })}
      {more && (
        <p
          className="absolute bottom-7 left-1.5 rounded bg-card/90 px-1.5 py-0.5 text-[0.6875rem] font-semibold text-[color:var(--text-secondary)]"
          data-testid="chart-lines-more"
        >
          {more}
        </p>
      )}
    </div>
  )
}
