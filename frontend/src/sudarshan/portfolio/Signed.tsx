// SUDARSHAN M7.1-M7.4, M7.23: a figure that can go negative, told by sign and arrow as well as
// colour. The sign is part of the text (a true minus), the arrow is the dashboard's TrendArrow,
// and a greyed (stale) figure keeps both. Numbers come through dashboard/format.ts.
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatRupees } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { PAISE_PER_RUPEE } from './types'

/** A percentage text from the money helpers ("-4.1000") as a number; null when there is none. */
export function pctNumber(text: string | null | undefined): number | null {
  if (text === null || text === undefined) return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

/**
 * paise as a signed rupee figure with its arrow, and the percentage under or beside it.
 * `balance` is for a figure that is normally positive (funds): no plus sign and no arrow until it
 * goes negative.
 */
export function Signed({
  paise,
  pct,
  stale = false,
  balance = false,
  decimals = 2,
  className,
  pctClassName,
}: {
  paise: number
  pct?: string | null
  stale?: boolean
  balance?: boolean
  decimals?: number
  className?: string
  pctClassName?: string
}) {
  const rupees = paise / PAISE_PER_RUPEE
  const direction = directionOf(rupees, decimals)
  const quiet = balance && direction !== 'down'
  const shownPct = pctNumber(pct)
  const tone = stale ? 'text-muted-foreground' : quiet ? 'text-foreground' : TREND_TEXT[direction]
  return (
    <span className={cn('inline-flex flex-wrap items-baseline gap-x-1.5', className)}>
      <span className={cn('inline-flex items-center gap-1 whitespace-nowrap', tone)}>
        {!quiet && <TrendArrow direction={direction} className="size-[0.5625rem]" />}
        {formatRupees(rupees, { decimals, signed: !balance })}
      </span>
      {shownPct !== null && (
        <span className={cn('whitespace-nowrap', tone, pctClassName)}>
          {formatPercent(shownPct)}
        </span>
      )}
    </span>
  )
}
