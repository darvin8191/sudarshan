import { memo } from 'react'
import type { AlertRow, StatusLine } from '@/api/marketAlerts'
import type { SymbolData } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { CARD, STALE } from '@/sudarshan/books/cards/parts'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { priceOf } from '@/sudarshan/dashboard/live'
import { auctionWaiting } from '@/sudarshan/live/auction'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { describe, statusLine } from '@/sudarshan/watchlist/rows'
import { AlertStatusChip, ConditionText, LineText } from './lines'
import { measure, money } from './measure'

/** Inside its closing auction (from the end of continuous trading until the last print): no trades are due. */
export function inAuctionWindow(row: Pick<AlertRow, 'auction'>, now: number): boolean {
  return row.auction !== null && row.auction.from <= now && now < row.auction.until
}

/** The line a card shows in its auction window in place of the freshness mark: the server's. */
export function auctionLine(row: Pick<AlertRow, 'auction' | 'check'>): StatusLine | null {
  if (!row.auction) return null
  return (
    row.check ?? {
      code: 'closing_auction',
      line: auctionWaiting(row.auction.close),
      real_time: false,
    }
  )
}

/** "Fired 2 times today"; nothing before the first. */
export function firedWords(count: number): string {
  if (count <= 0) return ''
  return count === 1 ? 'Fired once today' : `Fired ${count} times today`
}

/**
 * The figure an alert watches and its distance from firing, from the page's own ticks, with the
 * freshness mark the app's live screens use (M3.7): "Real-time" only while ticks come, the age
 * and grey once they stop where they are due, "Market closed" from the server's session line. In
 * a closing auction no trade is due, so the server's auction line stands in its place.
 */
export function PriceLine({
  row,
  line,
  feed,
  now,
  marketOpen,
}: {
  row: AlertRow
  line: SymbolData | undefined
  feed: FeedState
  now: number
  marketOpen: boolean | null
}) {
  const figure = measure(row, line?.data)
  const tick = priceOf(line)
  const auction = inAuctionWindow(row, now) ? auctionLine(row) : null
  const fresh = statusLine({
    now,
    marketOpen,
    lastTickAt: tick?.live ? tick.at : null,
    lastUpdateAt: tick?.at ?? null,
    connected: feed.connected,
    fallback: feed.fallback,
  })
  const stale = auction === null && 'stale' in fresh && fresh.stale
  return (
    <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 text-[0.8125rem]">
      <span
        data-stale={stale || undefined}
        className={cn('inline-flex flex-wrap items-baseline gap-x-1.5', stale && STALE)}
      >
        <span className="font-medium text-[color:var(--text-tertiary)]">{figure.label}</span>
        {figure.value === null ? (
          <span>
            <span aria-hidden="true">&mdash;</span>
            <span className="sr-only">No price yet</span>
          </span>
        ) : (
          <span className="font-semibold text-foreground">{money(row, figure.value)}</span>
        )}
        {figure.distance && (
          <span className="font-medium text-[color:var(--text-secondary)]">
            · {figure.distance}
          </span>
        )}
      </span>
      {auction ? <LineText line={auction} /> : <LiveLine line={fresh} now={now} />}
    </span>
  )
}

/**
 * One alert on the Alerts page (M10.3), top to bottom: the instrument and its status, the
 * condition in the server's words with its direction, the live figure and its distance (when the
 * page follows the instrument), the meta line and the fires today, why it stopped or paused, and
 * its check when it is not simply watching (left out where it repeats the engine line, or the
 * price line already shows it).
 */
export const AlertCard = memo(function AlertCard({
  row,
  line,
  followed,
  feed,
  now,
  marketOpen,
  engine,
  onOpen,
}: {
  row: AlertRow
  line: SymbolData | undefined
  /** The page follows this instrument's ticks (it follows at most 100). */
  followed: boolean
  feed: FeedState
  now: number
  marketOpen: boolean | null
  engine: StatusLine | null
  onOpen: (id: number) => void
}) {
  const named = describe(row.symbol, row.exchange)
  const meta = [row.words.meta, firedWords(row.fired_today)].filter(Boolean).join(' · ')
  const shownInPrice = followed && inAuctionWindow(row, now)
  const check =
    row.check &&
    !shownInPrice &&
    !(engine && engine.code === row.check.code && engine.line === row.check.line)
      ? row.check
      : null
  return (
    <button type="button" data-alert={row.id} onClick={() => onOpen(row.id)} className={CARD}>
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-[0.9375rem] font-bold">{named.title}</span>
          <span className="block truncate text-xs font-medium text-[color:var(--text-tertiary)]">
            {named.detail}
          </span>
        </span>
        <AlertStatusChip status={row.status} />
      </span>
      <ConditionText row={row} className="text-sm" />
      {followed && (
        <PriceLine row={row} line={line} feed={feed} now={now} marketOpen={marketOpen} />
      )}
      {meta && (
        <span className="text-xs font-medium text-[color:var(--text-tertiary)]">{meta}</span>
      )}
      {row.status !== 'active' && row.status_reason && (
        <span className="text-xs font-semibold text-[color:var(--text-secondary)]">
          {row.status_reason}
        </span>
      )}
      {check && <LineText line={check} />}
    </button>
  )
})
