import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { chartPath } from './prefs'

interface Props {
  exchange: string
  symbol: string
  /** What to wrap: a symbol cell's content. Defaults to the symbol itself. */
  children?: ReactNode
  className?: string
}

/**
 * Opens the phone chart for an instrument. Wrap it around a symbol in a list
 * or table; it is a plain link, so a long press or a middle click can open
 * the chart in a new tab and leave the list where it was.
 */
export function SymbolChartLink({ exchange, symbol, children, className }: Props) {
  return (
    <Link
      to={chartPath(symbol, exchange)}
      aria-label={`Open the chart for ${symbol} on ${exchange}`}
      className={cn(
        'rounded-sm underline-offset-4 hover:text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className
      )}
    >
      {children ?? symbol}
    </Link>
  )
}
