// SUDARSHAN M12.1 (GA10.4): the card's P&L line. The held contract's row comes with the page's
// read, from the book of the strategy's own mode (the sandbox's for a Sandbox strategy), and the
// books' own tick pipeline moves it: the broker's P&L moved by the price since the read
// (books/prices.ts). Read only: nothing here can send an order. "Real-time" only while ticks
// arrive; otherwise the time the figure was last read.
import { Clock } from 'lucide-react'
import { useMemo } from 'react'
import type { PriceableItem } from '@/hooks/useLivePrice'
import { units } from '@/sudarshan/books/cards/format'
import { Money } from '@/sudarshan/books/cards/parts'
import { useFramePrices } from '@/sudarshan/books/cards/useFramePrices'
import { useBookFeed } from '@/sudarshan/books/feed'
import type { MarketOpen } from '@/sudarshan/dashboard/live'
import type { Strategy } from './types'
import { whenText } from './words'

const NONE: PriceableItem[] = []
/** The card needs no market calendar: a figure is real-time while its ticks arrive. */
const NOT_KNOWN: MarketOpen = () => null

/** Where the figure comes from, as the card says it. */
const BOOK = { sandbox: 'the Sandbox book', live: "Upstox's positions" } as const

export function HeldPnl({ strategy }: { strategy: Strategy }) {
  const book = strategy.held_book
  const symbol = strategy.held_symbol
  const exchange = strategy.held_exchange
  const open = book !== null && book.quantity !== 0 && symbol !== null && exchange !== null
  const items = useMemo<PriceableItem[]>(
    () =>
      open && book && symbol && exchange
        ? [
            {
              symbol,
              exchange,
              quantity: book.quantity,
              average_price: book.average_price,
              ltp: book.ltp ?? undefined,
              pnl: book.pnl ?? undefined,
              today_realized_pnl: book.today_realized_pnl,
            },
          ]
        : NONE,
    [open, book, symbol, exchange]
  )
  const { rows, feed } = useFramePrices({
    items,
    bookAt: book?.priced_ms ?? null,
    marketOpen: NOT_KNOWN,
    enabled: open,
  })
  const { line } = useBookFeed(
    rows.map((row) => ({
      exchange: row.item.exchange,
      ltp: row.ltp ?? undefined,
      quantity: row.item.quantity,
      _at: row.at,
      _live: row.live,
    })),
    feed,
    NOT_KNOWN
  )

  const where = BOOK[strategy.mode]
  if (book === null) {
    return (
      <span data-testid="auto-pnl">
        The P&L could not be read from {where}. It is read again when you come back to this page.
      </span>
    )
  }
  if (!open) {
    return (
      <span data-testid="auto-pnl">
        Not in {where} now.
        {strategy.mode === 'live' && ' A new fill can take a minute to show there.'}
      </span>
    )
  }
  const row = rows[0]
  const live = line.kind === 'live' && row?.live === true
  const at = row?.at ?? book.priced_ms
  return (
    <span data-testid="auto-pnl" className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span className="whitespace-nowrap">
        {book.quantity > 0 ? 'Long' : 'Short'} {units(Math.abs(book.quantity))}
      </span>
      <span aria-hidden="true">·</span>
      <Money value={row?.pnl ?? null} className="font-semibold" />
      {live ? (
        <span
          data-feed="live"
          className="inline-flex items-center gap-1.5 text-xs font-semibold whitespace-nowrap text-[color:var(--live-ink)]"
        >
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-current" />
          Real-time
        </span>
      ) : (
        <span
          data-feed="read"
          className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap text-[color:var(--text-secondary)]"
        >
          <Clock aria-hidden="true" className="size-3" />
          Read at {whenText(at)}
        </span>
      )}
    </span>
  )
}
