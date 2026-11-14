import { CandlestickChart, ChevronRight, Clock } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { chartHref } from '@/lib/trading/chartLink'
import { cn } from '@/lib/utils'
import { units } from '@/sudarshan/books/cards/format'
import { istTime } from '@/sudarshan/books/time'
import { chartPath } from '@/sudarshan/chart/prefs'
import { usePhoneLayout } from '@/sudarshan/chart/usePhoneLayout'
import { contractWords, directionOf, formatRupees } from './format'
import type { PositionRow } from './live'
import { TREND_TEXT, TrendArrow } from './Trend'

/** How many positions the card lists before "N more" (decision 4 default). */
export const SHOWN_POSITIONS = 5

/** What a family member's mask hides (design 7): each hidden figure reads "Hidden". */
export interface RowMask {
  quantity?: boolean
  pnl?: boolean
}

const MINUS = '−'

/**
 * The contract's cell, opening its chart where the device is best served, as BookSymbolLink does
 * (the phone chart on a phone, the terminal on a wide screen), with the name a broker's app shows
 * in place of the raw symbol, which a phone row cannot fit. The whole cell is the 48dp target.
 */
function ContractLink({ row, children }: { row: PositionRow; children: ReactNode }) {
  const phone = usePhoneLayout()
  return (
    <Link
      to={phone ? chartPath(row.symbol, row.exchange) : chartHref(row.symbol, row.exchange)}
      title={`Open the chart for ${row.symbol}`}
      className="group flex min-h-12 min-w-0 flex-1 flex-col justify-center rounded-sm py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </Link>
  )
}

/**
 * The open positions in the P&L card (SUDARSHAN M3.3, D4): the contract (to its chart), its expiry,
 * the signed quantity and the product, and the P&L with arrow and sign, largest first, five at most and then "N more · See
 * all positions". Long and short are told by the sign and, for a screen reader, by Buy or Sell,
 * never by colour alone. Nothing here can be pressed but the chart and the Positions page: Exit,
 * Add and Exit all stay on Positions. A row no tick has moved since the broker's read is grey
 * with a clock, and the line under the rows says when that read was.
 */
export function PositionRows({
  rows,
  readAt,
  mask = {},
}: {
  rows: PositionRow[]
  readAt: number | null
  mask?: RowMask
}) {
  if (rows.length === 0) return null
  const shown = rows.slice(0, SHOWN_POSITIONS)
  const more = rows.length - shown.length
  const anyAsRead = shown.some((row) => !row.ticking)
  return (
    <div className="mt-1 flex flex-col">
      <ul
        aria-label="Open positions"
        className="flex flex-col divide-y divide-[color:var(--divider)]"
      >
        {shown.map((row) => {
          const direction = row.pnl === null ? 'flat' : directionOf(row.pnl)
          const side = row.quantity > 0 ? 'Buy' : 'Sell'
          const signed = `${row.quantity > 0 ? '+' : MINUS}${units(Math.abs(row.quantity))}`
          const { name, expiry } = contractWords(row.symbol)
          return (
            <li key={row.key} className="flex min-h-12 items-center gap-2 text-[0.8125rem]">
              <ContractLink row={row}>
                <span className="flex min-w-0 items-center gap-1.5 font-semibold group-hover:text-primary">
                  <span className="truncate group-hover:underline">
                    {name}
                    <span className="sr-only">, </span>
                  </span>
                  <CandlestickChart
                    aria-hidden="true"
                    className="size-3.5 shrink-0 text-muted-foreground"
                  />
                </span>
                <span className="truncate text-[0.75rem] font-medium text-[color:var(--text-tertiary)]">
                  {[expiry, mask.quantity ? 'Hidden' : signed, row.product]
                    .filter(Boolean)
                    .join(' · ')}
                  <span className="sr-only">{mask.quantity ? '' : `, ${side}`}</span>
                </span>
              </ContractLink>
              <span
                className={cn(
                  'flex min-w-[5.75rem] shrink-0 items-center justify-end gap-1 text-[0.875rem] font-bold',
                  mask.pnl || row.pnl === null
                    ? 'text-muted-foreground'
                    : row.ticking
                      ? TREND_TEXT[direction]
                      : 'text-[color:var(--text-secondary)]'
                )}
              >
                {mask.pnl || row.pnl === null ? (
                  mask.pnl ? (
                    'Hidden'
                  ) : (
                    '–'
                  )
                ) : (
                  <>
                    {row.ticking ? (
                      <TrendArrow direction={direction} className="size-2.5" />
                    ) : (
                      <Clock aria-hidden="true" className="size-3.5" />
                    )}
                    {formatRupees(row.pnl, { signed: true })}
                    {!row.ticking && <span className="sr-only"> as the broker read it</span>}
                  </>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      {anyAsRead && readAt !== null && (
        <p className="flex items-center gap-1 text-[0.75rem] font-medium text-[color:var(--text-tertiary)]">
          <Clock aria-hidden="true" className="size-3" />
          No tick yet on rows with a clock · broker read {istTime(readAt)}
        </p>
      )}
      {more > 0 && (
        <Link
          to="/positions"
          className="-mb-1 flex min-h-12 items-center justify-between gap-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          {more} more · See all positions
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      )}
    </div>
  )
}
