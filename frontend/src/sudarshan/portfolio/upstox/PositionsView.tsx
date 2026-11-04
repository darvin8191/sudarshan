// SUDARSHAN M7.1, M7.3: the Upstox positions, read-only, with no actions. Orders are placed in
// Positions; this part only reads the account.
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { keyOf } from '@/sudarshan/dashboard/live'
import { TrendArrow } from '@/sudarshan/dashboard/Trend'
import { clockOfInstant } from '../days'
import { Signed } from '../Signed'
import { notLiveWords } from '../statusWords'
import { priceText, quantityText } from '../tab/format'
import type { UpstoxPart, UpstoxPosition } from '../types'
import { type PositionNow, positionNow } from './live'
import { CARD, CHIP, Figure, LINK, NoFigures, PartNote, ReadStamp } from './parts'
import { instrumentOf, type UpstoxLive } from './useUpstoxLive'

function side(qtyU: number): string {
  if (qtyU > 0) return 'Long'
  if (qtyU < 0) return 'Short'
  return 'Closed'
}

export function PositionsView({
  positions,
  mtf,
  live,
}: {
  positions: UpstoxPart<UpstoxPosition[]> | undefined
  mtf: UpstoxPart<UpstoxPosition[]> | undefined
  live: UpstoxLive
}) {
  const rows = positions?.data
  if (!rows) return <NoFigures part={positions}>The positions could not be read yet.</NoFigures>
  const readAt = positions.read_at
  const figures = (list: UpstoxPosition[], at: number | null) =>
    list.map((row) => {
      const key = instrumentOf(row)
      return positionNow(row, key ? live.ticks.get(key) : undefined, at)
    })
  const now = figures(rows, readAt)
  const mtfRows = mtf?.data ?? []
  const mtfNow = figures(mtfRows, mtf?.read_at ?? null)
  const total = [...now, ...mtfNow].reduce((sum, row) => sum + row.pnlPaise, 0)
  const allStale = live.line.kind === 'delayed' && live.line.stale

  return (
    <div className="flex flex-col gap-3">
      <PartNote part={positions} />
      <PartNote part={mtf} />
      <section
        aria-label="Positions summary"
        className={cn(CARD, 'flex flex-col gap-3 px-4 py-3.5')}
      >
        <dl>
          <Figure label="Total P&L">
            <Signed paise={total} stale={allStale} />
          </Figure>
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <LiveLine line={live.line} now={live.now} />
          <ReadStamp at={readAt} />
        </div>
      </section>
      {rows.length === 0 ? (
        <NoFigures part={undefined}>No open positions in your Upstox account.</NoFigures>
      ) : (
        <PositionList rows={rows} now={now} readAt={readAt} live={live} label="Positions" />
      )}
      {mtfRows.length > 0 && (
        <>
          <h2 className="text-[0.9375rem] font-bold">MTF positions</h2>
          <PositionList
            rows={mtfRows}
            now={mtfNow}
            readAt={mtf?.read_at ?? null}
            live={live}
            label="MTF positions"
          />
        </>
      )}
      <Link to="/positions" className={LINK}>
        Open Positions
        <ChevronRight aria-hidden="true" className="size-4" />
      </Link>
    </div>
  )
}

function PositionList({
  rows,
  now,
  readAt,
  live,
  label,
}: {
  rows: UpstoxPosition[]
  now: PositionNow[]
  readAt: number | null
  live: UpstoxLive
  label: string
}) {
  return (
    <ul aria-label={label} className="flex flex-col gap-2">
      {rows.map((row, n) => {
        const figures = now[n]
        const direction = live.directions.get(keyOf(row.symbol ?? '', row.exchange ?? '')) ?? null
        const stale = live.stale({ ...row, held: row.qty_u !== 0 })
        const priceWords = figures.moved
          ? 'Real-time'
          : notLiveWords(readAt === null ? null : clockOfInstant(readAt))
        return (
          <li
            key={`${row.instrument_key}:${row.product}`}
            className={cn(CARD, 'flex flex-col gap-2 px-4 py-3')}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-base font-bold">{row.symbol ?? row.name}</p>
                <p className="text-sm text-[color:var(--text-secondary)]">
                  {side(row.qty_u)} {row.qty_u === 0 ? '' : quantityText(Math.abs(row.qty_u))}
                  {row.avg_price_u > 0 ? ` · avg ${priceText(row.avg_price_u)}` : ''}
                </p>
              </div>
              <div className="text-right">
                <p className="flex items-center justify-end gap-1 text-base font-semibold">
                  {direction && <TrendArrow direction={direction} className="size-[0.5625rem]" />}
                  {priceText(figures.priceU)}
                </p>
                <p className="text-xs text-[color:var(--text-tertiary)]">{priceWords}</p>
              </div>
            </div>
            <div className="flex items-end justify-between gap-2">
              <span className={CHIP}>{row.product}</span>
              <span className="flex flex-col items-end">
                <span className="text-xs font-semibold text-muted-foreground">P&amp;L</span>
                <Signed paise={figures.pnlPaise} stale={stale} />
              </span>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
