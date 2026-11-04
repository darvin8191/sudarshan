// SUDARSHAN M7.1, M7.3: the Upstox holdings, read-only. Upstox's own figures at the read, moved
// only by ticks newer than it (live.ts). Cards open a sheet with the details and the links out.
import { ChevronRight } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { keyOf } from '@/sudarshan/dashboard/live'
import { TrendArrow } from '@/sudarshan/dashboard/Trend'
import { clockOfInstant } from '../days'
import { Signed } from '../Signed'
import { notLiveWords, TODAY_WORDS } from '../statusWords'
import { priceText, quantityText, rupeesText } from '../tab/format'
import type { UpstoxHolding, UpstoxPart } from '../types'
import { HoldingSheet } from './HoldingSheet'
import { type HoldingNow, holdingNow, holdingsTotals } from './live'
import { CARD, CHIP, Figure, LINK, NoFigures, PartNote, ReadStamp } from './parts'
import { instrumentOf, type UpstoxLive } from './useUpstoxLive'

export function HoldingsView({
  part,
  live,
}: {
  part: UpstoxPart<UpstoxHolding[]> | undefined
  live: UpstoxLive
}) {
  const [open, setOpen] = useState<string | null>(null)
  const rows = part?.data
  if (!rows) return <NoFigures part={part}>The holdings could not be read yet.</NoFigures>
  if (rows.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <PartNote part={part} />
        <NoFigures part={part}>No holdings in your Upstox account.</NoFigures>
        <ReadStamp at={part?.read_at ?? null} />
      </div>
    )
  }
  const readAt = part.read_at
  const now = rows.map((row) => {
    const key = instrumentOf(row)
    return holdingNow(row, key ? live.ticks.get(key) : undefined, readAt)
  })
  const totals = holdingsTotals(now)
  const chosen = rows.findIndex((row) => row.instrument_key === open)
  const allStale = live.line.kind === 'delayed' && live.line.stale

  return (
    <div className="flex flex-col gap-3">
      <PartNote part={part} />
      <section
        aria-label="Holdings summary"
        className={cn(CARD, 'flex flex-col gap-3 px-4 py-3.5')}
      >
        <dl className="grid grid-cols-2 gap-3">
          <Figure label="Current value" note={`Invested ${rupeesText(totals.investedPaise)}`}>
            <span className={cn(allStale && 'text-muted-foreground')}>
              {rupeesText(totals.valuePaise)}
            </span>
          </Figure>
          <Figure label="Overall P&L">
            <Signed
              paise={totals.pnlPaise}
              pct={totals.pnlPct}
              stale={allStale}
              className="flex-col items-start gap-0"
            />
          </Figure>
          <Figure
            label="Today"
            note={
              totals.todayUnknown > 0 && totals.todayPaise !== null
                ? `${totals.todayUnknown} holding${totals.todayUnknown === 1 ? '' : 's'} not known yet`
                : undefined
            }
          >
            {totals.todayPaise === null ? (
              <span className="text-base font-semibold text-[color:var(--text-secondary)]">
                {TODAY_WORDS.unknown}
              </span>
            ) : (
              <Signed paise={totals.todayPaise} stale={allStale} />
            )}
          </Figure>
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <LiveLine line={live.line} now={live.now} />
          <ReadStamp at={readAt} />
        </div>
      </section>
      <ul className="flex flex-col gap-2">
        {rows.map((row, n) => (
          <li key={row.instrument_key}>
            <HoldingCard
              row={row}
              figures={now[n]}
              readAt={readAt}
              stale={live.stale({ ...row, held: row.qty_u !== 0 })}
              direction={live.directions.get(keyOf(row.symbol ?? '', row.exchange ?? '')) ?? null}
              onOpen={() => setOpen(row.instrument_key)}
            />
          </li>
        ))}
      </ul>
      <Link to="/portfolio-analyzer" className={LINK}>
        Portfolio Analyzer
        <ChevronRight aria-hidden="true" className="size-4" />
      </Link>
      <HoldingSheet
        row={chosen >= 0 ? rows[chosen] : null}
        figures={chosen >= 0 ? now[chosen] : null}
        readAt={readAt}
        onClose={() => setOpen(null)}
      />
    </div>
  )
}

function HoldingCard({
  row,
  figures,
  readAt,
  stale,
  direction,
  onOpen,
}: {
  row: UpstoxHolding
  figures: HoldingNow
  readAt: number | null
  stale: boolean
  direction: 'up' | 'down' | null
  onOpen: () => void
}) {
  const priceWords = figures.moved
    ? 'Real-time'
    : notLiveWords(readAt === null ? null : clockOfInstant(readAt))
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`${row.symbol ?? row.name}, details`}
      className={cn(
        CARD,
        'flex min-h-12 w-full flex-col gap-2 px-4 py-3 text-left outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'
      )}
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-base font-bold">{row.symbol ?? row.name}</span>
          <span className="block text-sm text-[color:var(--text-secondary)]">
            {quantityText(row.qty_u)} shares · avg {priceText(row.avg_price_u)}
          </span>
        </span>
        <span className="text-right">
          <span className="flex items-center justify-end gap-1 text-base font-semibold">
            {direction && <TrendArrow direction={direction} className="size-[0.5625rem]" />}
            {priceText(figures.priceU)}
          </span>
          <span className="block text-xs text-[color:var(--text-tertiary)]">{priceWords}</span>
        </span>
      </span>
      <span className="flex flex-wrap gap-1.5">
        {row.t1_qty_u > 0 && <span className={CHIP}>T1 {quantityText(row.t1_qty_u)}</span>}
        {row.collateral_qty_u > 0 && (
          <span className={CHIP}>Pledged {quantityText(row.collateral_qty_u)}</span>
        )}
      </span>
      <span className="grid grid-cols-2 gap-2 text-sm">
        <span className="flex flex-col">
          <span className="text-xs font-semibold text-muted-foreground">Today</span>
          {figures.todayPaise === null ? (
            <span className="text-[color:var(--text-secondary)]">Not known yet</span>
          ) : (
            <Signed paise={figures.todayPaise} pct={figures.todayPct} stale={stale} />
          )}
        </span>
        <span className="flex flex-col">
          <span className="text-xs font-semibold text-muted-foreground">P&amp;L</span>
          <Signed paise={figures.pnlPaise} pct={figures.pnlPct} stale={stale} />
        </span>
      </span>
    </button>
  )
}
