// components/trading/QuoteHeader.tsx
// Real-time quote header display for PlaceOrderDialog

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export interface QuoteHeaderProps {
  exchange: string
  ltp?: number
  prevClose?: number
  change?: number
  changePercent?: number
  bidPrice?: number
  askPrice?: number
  bidSize?: number
  askSize?: number
  isLoading?: boolean
}

// SUDARSHAN (v0.13.0 review M2): the D tokens, light only. The exchange is a word in a quiet
// outline (it was one of seven pale tints); the change takes --up / --down with its sign; the
// bid is on the buy side and the ask on the sell side, each beside its word.
const EXCHANGE_BADGE =
  'border-[color:var(--border-strong)] bg-transparent text-[color:var(--text-secondary)]'

export function QuoteHeader({
  exchange,
  ltp,
  prevClose,
  change,
  changePercent,
  bidPrice,
  askPrice,
  bidSize,
  askSize,
  isLoading,
}: QuoteHeaderProps) {
  // Calculate change from prevClose if not provided
  const displayChange = change ?? (ltp && prevClose ? ltp - prevClose : undefined)
  const displayChangePercent =
    changePercent ?? (displayChange && prevClose ? (displayChange / prevClose) * 100 : undefined)

  const isPositive = displayChange !== undefined && displayChange >= 0

  if (isLoading) {
    return (
      // SUDARSHAN M1.21 (T4): a still placeholder while the quote loads, never a pulse.
      <div className="p-3 bg-muted/30 rounded-lg">
        <div className="h-4 bg-muted rounded w-24 mb-2" />
        <div className="h-6 bg-muted rounded w-32" />
      </div>
    )
  }

  return (
    <div className="p-3 bg-muted/30 rounded-lg space-y-2">
      {/* Exchange Badge and LTP Row */}
      <div className="flex items-center justify-between">
        <Badge className={cn('text-[10px] px-1.5 py-0', EXCHANGE_BADGE)}>{exchange}</Badge>
        <div className="flex items-center gap-2">
          <span className="text-xl font-bold">{ltp !== undefined ? ltp.toFixed(2) : '-'}</span>
          {displayChange !== undefined && (
            <span
              className={cn(
                'text-sm font-medium',
                isPositive ? 'text-[color:var(--up)]' : 'text-[color:var(--down)]'
              )}
            >
              {isPositive ? '+' : ''}
              {displayChange.toFixed(2)}
              {displayChangePercent !== undefined && (
                <span className="ml-1">
                  ({isPositive ? '+' : ''}
                  {displayChangePercent.toFixed(2)}%)
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {/* Bid/Ask Row */}
      <div className="flex items-center justify-between text-sm border-t border-border/50 pt-2">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Bid:</span>
          <span className="text-[color:var(--buy-solid)] font-medium font-mono">
            {bidPrice !== undefined && bidPrice > 0 ? bidPrice.toFixed(2) : '-'}
          </span>
          {bidSize !== undefined && bidSize > 0 && (
            <span className="text-muted-foreground text-xs">x{bidSize.toLocaleString()}</span>
          )}
        </div>
        <div className="text-muted-foreground">|</div>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Ask:</span>
          <span className="text-[color:var(--sell-solid)] font-medium font-mono">
            {askPrice !== undefined && askPrice > 0 ? askPrice.toFixed(2) : '-'}
          </span>
          {askSize !== undefined && askSize > 0 && (
            <span className="text-muted-foreground text-xs">x{askSize.toLocaleString()}</span>
          )}
        </div>
      </div>
    </div>
  )
}
