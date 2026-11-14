// SUDARSHAN P6 D4: the member's home screen. The market strip, then a glance at the owner's
// positions. Every figure that can be hidden by the member's mask is "Hidden" when absent and a
// dash when not known (Y24), and the strip says what the feed is doing, never "Live": it is
// Real-time only while ticks arrive (rule 7).
import { useQuery } from '@tanstack/react-query'
import { EyeOff } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { ProblemCard, STALE } from '@/sudarshan/books/cards/parts'
import {
  Figure,
  HiddenTotal,
  ReadStamp,
  ServiceWords,
  unreadWords,
} from '@/sudarshan/books/cards/readOnlyParts'
import { formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { FamilyError, familyApi, NO_ANSWER } from '../api'
import { instrumentOf, isOpen, type MaskedPosition, positionFigures, totalOf } from '../books'
import { useFamilyFeed } from '../familyManager'
import { familyMarketOpen } from '../feedLine'
import type { Quote } from '../types'
import { useFamilyBoard } from '../useBoard'
import { useFamilyScreen } from '../useBook'
import { useViewerLive } from '../useViewerLive'

const MARKET_KEY = ['family', 'market'] as const

function Index({ quote, ltp, stale }: { quote: Quote; ltp: number | null; stale: boolean }) {
  return (
    <li
      data-index={quote.symbol}
      data-stale={stale || undefined}
      className="flex min-h-12 flex-col justify-center gap-px rounded-[1rem] border bg-card px-3 py-2"
    >
      <span className="truncate text-xs font-semibold text-muted-foreground">{quote.name}</span>
      <span className={cn('text-[0.9375rem] font-bold', stale && STALE)}>
        {ltp === null ? '-' : formatPrice(ltp)}
        {stale && <span className="sr-only"> Stale</span>}
      </span>
    </li>
  )
}

export default function FamilyDashboard() {
  useFamilyScreen('dashboard')
  const feed = useFamilyFeed().state
  const market = useQuery({
    queryKey: MARKET_KEY,
    queryFn: ({ signal }) => familyApi.market(signal),
    retry: false,
    staleTime: 0,
  })
  const positions = useFamilyBoard<MaskedPosition>('positions')
  const marketOpen = useMemo(() => familyMarketOpen(feed), [feed])

  const quotes = market.data?.indices ?? []
  // Review M3 (v0.12.0): the glance follows the open positions' own ticks, as the Positions
  // screen does. It was worked out from the read alone, so the home screen's one big figure was
  // up to 45 s old under a line that said Real-time (rule 7). The indices come first, so the cap
  // on followed symbols takes positions last.
  const open = useMemo(() => positions.rows.filter(isOpen), [positions.rows])
  const instruments = useMemo(
    () => [
      ...quotes.map((quote) => ({ symbol: quote.symbol, exchange: quote.exchange })),
      ...open.map(instrumentOf).filter((i): i is NonNullable<typeof i> => i !== null),
    ],
    [quotes, open]
  )
  const live = useViewerLive({ instruments, marketOpen })

  const held = open
  const total = totalOf(
    positions.rows.map((row) => {
      const instrument = instrumentOf(row)
      const tick = instrument
        ? live.ticks.get(`${instrument.exchange.toUpperCase()}:${instrument.symbol.toUpperCase()}`)
        : undefined
      return positionFigures(row, tick, positions.readAt).pnl
    })
  )
  // Stale on the Positions screen's rule: the feed is late or coming back with old figures.
  const totalStale =
    held.length > 0 &&
    (live.line.kind === 'delayed' || live.line.kind === 'connecting') &&
    live.line.stale
  const unread = unreadWords(
    positions.state,
    positions.words,
    positions.rows.length,
    positions.readAt
  )
  const problem = market.error
    ? market.error instanceof FamilyError
      ? market.error.message
      : NO_ANSWER
    : null

  return (
    <div className="flex flex-col gap-3">
      <section aria-labelledby="sd-family-market" className="flex flex-col gap-2">
        <div className="flex min-h-5 items-center justify-between gap-3 px-1">
          <h2
            id="sd-family-market"
            className="text-[0.84375rem] font-semibold text-muted-foreground"
          >
            Market
          </h2>
          <LiveLine line={live.line} now={live.now} />
        </div>
        {problem && quotes.length === 0 ? (
          <ProblemCard message={problem} onRetry={() => void market.refetch()} />
        ) : (
          <ul aria-label="Indices" className="grid grid-cols-2 gap-2">
            {quotes.map((quote) => {
              const tick = live.ticks.get(
                `${quote.exchange.toUpperCase()}:${quote.symbol.toUpperCase()}`
              )
              return (
                <Index
                  key={`${quote.exchange}:${quote.symbol}`}
                  quote={quote}
                  ltp={tick?.ltp ?? quote.ltp}
                  stale={live.stale({ symbol: quote.symbol, exchange: quote.exchange })}
                />
              )
            })}
          </ul>
        )}
      </section>

      <section
        aria-labelledby="sd-family-glance"
        className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
      >
        <h2 id="sd-family-glance" className="text-[0.84375rem] font-semibold text-muted-foreground">
          Positions today
        </h2>
        {unread === null && <ServiceWords state={positions.state} words={positions.words} />}
        {positions.rows.length === 0 && positions.loading ? (
          <p className="text-sm text-muted-foreground">Reading the account</p>
        ) : positions.rows.length === 0 && positions.error ? (
          <ProblemCard message={positions.error} onRetry={positions.onRetry} />
        ) : unread !== null ? (
          // Review M4: not read is not flat. The sentence stands where the figure would, and
          // there is no zero and no count.
          <output data-testid="glance-unread" className="flex items-start gap-2 py-1">
            <EyeOff
              aria-hidden="true"
              className="mt-0.5 size-5 shrink-0 text-[color:var(--text-secondary)]"
            />
            <span className="text-base font-bold">{unread}</span>
          </output>
        ) : (
          <>
            <p data-testid="glance-total" className="flex items-baseline gap-2">
              {total === undefined ? (
                <HiddenTotal />
              ) : (
                <Figure
                  value={total}
                  stale={totalStale}
                  className="sd-display text-[1.75rem] font-bold"
                />
              )}
            </p>
            <p className="text-sm font-medium text-[color:var(--text-secondary)]">
              {held.length === 0
                ? 'No open positions'
                : held.length === 1
                  ? '1 open position'
                  : `${held.length} open positions`}
            </p>
          </>
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          <Link
            to="/family/positions"
            className="inline-flex min-h-12 items-center rounded-xl border px-4 text-sm font-semibold hover:bg-accent"
          >
            Positions
          </Link>
          <Link
            to="/family/holdings"
            className="inline-flex min-h-12 items-center rounded-xl border px-4 text-sm font-semibold hover:bg-accent"
          >
            Holdings
          </Link>
        </div>
        <ReadStamp part="Positions" readAt={positions.readAt} />
      </section>
    </div>
  )
}
