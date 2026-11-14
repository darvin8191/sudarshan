/**
 * The phone chart on its side (M4.15, C10): the app bar, the price header,
 * the book strip, the timeframes, the tools and the footer fold into one
 * 48 dp strip, and the chart takes the rest of a short screen. From the left:
 * the way back; the instrument with the feed's state under it; the price and
 * its change; the timeframe; indicators, the chart style and Draw; Buy and Sell.
 * The book, open interest, search, alerts and keeping the screen on wait for
 * the phone to be turned upright again.
 */
import { ArrowLeft, ChevronDown } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { drawToolIcon } from '@/lib/trading/drawTools'
import { ChromeIcon } from '@/lib/trading/glyphs'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import { cn } from '@/lib/utils'
import { formatPercent, formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { type ChartStyle, intervalLabel, styleOf } from './prefs'
import { changeOn, type PriceStore, priceQuiet } from './prices'

const ICON =
  'inline-flex size-12 shrink-0 items-center justify-center rounded-full hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
/** A 48 dp target around a 40 dp pill, so Buy and Sell sit inside the strip rather than fill it. */
const ORDER = 'group h-12 shrink-0 px-0.5 focus-visible:outline-none disabled:opacity-45'
const PILL =
  'flex h-10 items-center rounded-xl px-4 text-sm font-bold tracking-[0.06em] text-white group-focus-visible:ring-2 group-focus-visible:ring-ring group-focus-visible:ring-offset-1'

export function SidewaysStrip({
  symbol,
  prices,
  prevClose,
  status,
  dp,
  timeframe,
  studies,
  style,
  orders,
  onBack,
  onTimeframe,
  onIndicators,
  onStyle,
  onOrder,
  drawing,
  onDraw,
}: {
  symbol: string
  prices: PriceStore
  prevClose: number | null
  status: LiveStatus | null
  dp(ltp: number): number
  timeframe: string
  /** How many indicators the set holds. */
  studies: number
  style: ChartStyle
  /** Buy and Sell: shown for what trades, off until its details load; none for an index. */
  orders: 'on' | 'off' | 'none'
  onBack(): void
  onTimeframe(): void
  onIndicators(): void
  onStyle(): void
  onOrder(side: 'BUY' | 'SELL'): void
  /** The drawing tools are over the chart (C7). */
  drawing: boolean
  onDraw(): void
}) {
  const { tick } = useSyncExternalStore(prices.subscribe, prices.get)
  const now = useNow()
  const ltp = tick?.ltp ?? null
  const decimals = dp(ltp ?? 0)
  const { change, percent, direction } = changeOn(ltp, prevClose, decimals)
  const quiet = priceQuiet(status)
  const look = styleOf(style)
  return (
    <header
      className="flex h-12 shrink-0 items-center gap-1 border-b bg-card pr-[max(0.25rem,env(safe-area-inset-right))] pl-[max(0.25rem,env(safe-area-inset-left))]"
      data-testid="chart-strip"
    >
      <button type="button" onClick={onBack} aria-label="Back" className={ICON}>
        <ArrowLeft aria-hidden="true" className="size-5" />
      </button>
      <div className="flex min-w-0 flex-1 flex-col justify-center overflow-hidden">
        <h1 className="truncate text-sm leading-tight font-semibold">{symbol}</h1>
        {status && <LiveLine line={status.line} now={now} className="leading-tight" />}
      </div>
      <section aria-label="Price" className="flex shrink-0 flex-col items-end leading-tight">
        <span
          className={cn('text-lg font-bold tabular-nums', quiet && 'text-muted-foreground')}
          data-testid="chart-ltp"
        >
          {ltp === null ? (
            <>
              <span aria-hidden="true">&mdash;</span>
              <span className="sr-only">No price yet</span>
            </>
          ) : (
            formatPrice(ltp, decimals)
          )}
        </span>
        {change !== null && percent !== null && (
          <span
            className={cn(
              'flex items-center gap-1 text-xs font-bold tabular-nums',
              TREND_TEXT[direction]
            )}
            data-testid="chart-change"
          >
            <TrendArrow direction={direction} className="size-2" />
            {formatRupees(change, { decimals, signed: true })} ({formatPercent(percent)})
          </span>
        )}
      </section>
      <button
        type="button"
        onClick={onTimeframe}
        aria-label={`Timeframe ${intervalLabel(timeframe)}, change`}
        className="inline-flex h-12 min-w-12 shrink-0 items-center justify-center gap-0.5 rounded-full px-2 text-sm font-semibold text-primary hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {intervalLabel(timeframe)}
        <ChevronDown aria-hidden="true" className="size-3 shrink-0" />
      </button>
      <button
        type="button"
        onClick={onIndicators}
        aria-label={studies > 0 ? `Indicators, ${studies} on the chart` : 'Indicators'}
        className={cn(ICON, 'relative')}
      >
        <ChromeIcon id="indicators" size={18} />
        {studies > 0 && (
          <span
            aria-hidden="true"
            className="absolute top-1.5 right-1 rounded-full bg-[color:var(--primary-tint)] px-1 text-[0.6875rem] font-bold text-primary"
          >
            {studies}
          </span>
        )}
      </button>
      <button
        type="button"
        onClick={onStyle}
        aria-label={`Chart style: ${look.label}`}
        className={ICON}
      >
        <ChromeIcon id={look.glyph} size={18} />
      </button>
      <button
        type="button"
        onClick={onDraw}
        aria-label="Draw"
        aria-pressed={drawing}
        className={cn(ICON, drawing && 'bg-[color:var(--primary-tint)] text-primary')}
      >
        {drawToolIcon('trend-line', 18)}
      </button>
      {orders !== 'none' && (
        <>
          <button
            type="button"
            disabled={orders === 'off'}
            onClick={() => onOrder('BUY')}
            className={ORDER}
          >
            <span className={cn(PILL, 'bg-[color:var(--buy-solid)]')}>BUY</span>
          </button>
          <button
            type="button"
            disabled={orders === 'off'}
            onClick={() => onOrder('SELL')}
            className={ORDER}
          >
            <span className={cn(PILL, 'bg-[color:var(--sell-solid)]')}>SELL</span>
          </button>
        </>
      )}
    </header>
  )
}
