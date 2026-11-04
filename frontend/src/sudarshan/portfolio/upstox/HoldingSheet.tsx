// SUDARSHAN M7.1: one Upstox holding in detail, with the links out. Nothing here places an order.
import { ChartCandlestick, ShoppingCart } from 'lucide-react'
import { Link } from 'react-router'
import { chartPath } from '@/sudarshan/chart/prefs'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { clockOfInstant } from '../days'
import { Signed } from '../Signed'
import { priceText, quantityText, rupeesText } from '../tab/format'
import type { UpstoxHolding } from '../types'
import type { HoldingNow } from './live'
import { LINK, ReadStamp } from './parts'

export function HoldingSheet({
  row,
  figures,
  readAt,
  onClose,
}: {
  row: UpstoxHolding | null
  figures: HoldingNow | null
  readAt: number | null
  onClose: () => void
}) {
  const facts: Array<[string, string]> =
    row && figures
      ? [
          ['Quantity', quantityText(row.qty_u)],
          ['Bought, arriving (T1)', quantityText(row.t1_qty_u)],
          ['Pledged', quantityText(row.collateral_qty_u)],
          ['Average price', priceText(row.avg_price_u)],
          [
            figures.moved
              ? 'Price now'
              : `Price at ${readAt === null ? 'the last read' : clockOfInstant(readAt)}`,
            priceText(figures.priceU),
          ],
          ['Invested', rupeesText(figures.investedPaise)],
          ['Value', rupeesText(figures.valuePaise)],
          ['Product', row.product],
          ...(row.isin ? ([['ISIN', row.isin]] as Array<[string, string]>) : []),
        ]
      : []
  return (
    <BottomSheet
      open={row !== null}
      onOpenChange={(next) => !next && onClose()}
      title={row?.name ?? ''}
      description={row?.symbol ?? undefined}
    >
      {row && figures && (
        <div className="flex flex-col gap-3 px-4 pb-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            <span className="flex flex-col">
              <span className="text-xs font-semibold text-muted-foreground">P&amp;L</span>
              <Signed paise={figures.pnlPaise} pct={figures.pnlPct} />
            </span>
            <ReadStamp at={readAt} />
          </div>
          <dl className="divide-y divide-[color:var(--divider)]">
            {facts.map(([label, value]) => (
              <div key={label} className="flex min-h-12 items-center justify-between gap-3 text-sm">
                <dt className="text-[color:var(--text-secondary)]">{label}</dt>
                <dd className="font-semibold">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap gap-2">
            {row.symbol && row.exchange && (
              <Link to={chartPath(row.symbol, row.exchange)} className={LINK}>
                <ChartCandlestick aria-hidden="true" className="size-4" />
                Chart
              </Link>
            )}
            <Link to="/holdings" className={LINK}>
              <ShoppingCart aria-hidden="true" className="size-4" />
              Trade in Holdings
            </Link>
          </div>
        </div>
      )}
    </BottomSheet>
  )
}
