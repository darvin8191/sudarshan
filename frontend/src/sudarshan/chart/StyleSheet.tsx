/**
 * The chart's style (M4.15, C2): candles, hollow candles, bars, line or Heikin
 * Ashi, one choice for every instrument on this phone. Each is drawn with the
 * chart engine's own glyph and named in words; the one in use carries a tick.
 */
import { Check } from 'lucide-react'
import { ChromeIcon } from '@/lib/trading/glyphs'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { CHART_STYLES, type ChartStyle } from './prefs'

export function StyleSheet({
  open,
  onOpenChange,
  current,
  onPick,
}: {
  open: boolean
  onOpenChange(open: boolean): void
  current: ChartStyle
  onPick(style: ChartStyle): void
}) {
  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Chart style"
      description="For every instrument on this phone"
    >
      <ul className="flex flex-col">
        {CHART_STYLES.map((style) => {
          const chosen = style.value === current
          return (
            <li key={style.value}>
              <button
                type="button"
                aria-pressed={chosen}
                onClick={() => onPick(style.value)}
                className={cn(
                  'flex h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  chosen
                    ? 'bg-[color:var(--primary-tint)] text-primary shadow-[inset_0_0_0_1.5px_var(--primary)]'
                    : 'hover:bg-accent'
                )}
              >
                <ChromeIcon id={style.glyph} size={20} className="shrink-0" />
                {style.label}
                {chosen && <Check aria-hidden="true" className="ml-auto size-4 shrink-0" />}
              </button>
            </li>
          )
        })}
      </ul>
      <p className="pt-2 text-xs text-muted-foreground">
        Renko, Kagi, Point and Figure and the other styles are in the full terminal.
      </p>
    </BottomSheet>
  )
}
