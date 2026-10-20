import { CandlestickChart } from 'lucide-react'
import { ChartSymbolLink } from '@/components/trading/ChartSymbolLink'
import { SymbolChartLink } from './SymbolChartLink'
import { usePhoneLayout } from './usePhoneLayout'

interface Props {
  symbol: string
  exchange: string
  /**
   * What a wide screen shows: upstream's link into the /trading terminal (the
   * default, as the order book, trade book and positions always had), or the
   * bare symbol, for a book upstream left without a link (holdings).
   */
  wide?: 'terminal' | 'text'
}

/**
 * A symbol in a book row (positions, holdings, order book, trade book) that
 * opens its chart where the device is best served. On a phone it opens the
 * phone chart (/chart/<exchange>/<symbol>, M4.15), drawn like upstream's link
 * and a full touch target high. On a wider screen it is what upstream shows
 * there, unchanged.
 */
export function BookSymbolLink({ symbol, exchange, wide = 'terminal' }: Props) {
  const phone = usePhoneLayout()
  if (!phone) {
    if (wide === 'text') return symbol
    return <ChartSymbolLink symbol={symbol} exchange={exchange} />
  }
  return (
    <SymbolChartLink
      symbol={symbol}
      exchange={exchange}
      className="group inline-flex min-h-12 items-center gap-1.5 hover:no-underline"
    >
      <span className="underline-offset-4 group-hover:underline">{symbol}</span>
      <CandlestickChart
        aria-hidden="true"
        className="h-3.5 w-3.5 shrink-0 text-muted-foreground group-hover:text-primary"
      />
    </SymbolChartLink>
  )
}
