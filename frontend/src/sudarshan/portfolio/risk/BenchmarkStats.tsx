// SUDARSHAN P7 F6 (M7.31): the three figures a benchmark gains on Performance (design p7 9.2):
// beta, correlation and the excess return, signed and with "ahead" or "behind" in words, then what
// they rest on ("On 60 trading days to 13 Nov 2026") or the sentence that says when they will show.
// Statistics on closing values, not market data: nothing here moves with ticks or says "Real-time".

import { cn } from '@/lib/utils'
import { directionOf, formatAmount, formatPercent } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { pctNumber } from '../Signed'
import type { BenchmarkFigure } from '../types'

const LABEL = 'text-xs font-semibold text-muted-foreground'
const NOTE = 'text-[0.78125rem] font-medium text-[color:var(--text-secondary)]'

/** A ratio of four decimals as the phone shows it: two, with a true minus. */
export function ratioText(text: string | null): string | null {
  const n = pctNumber(text)
  if (n === null) return null
  return `${Number(n.toFixed(2)) < 0 ? '−' : ''}${formatAmount(n, 2)}`
}

/** The excess return in words: ahead of the index, behind it, or level with it. */
export function excessWord(pct: number): string {
  const direction = directionOf(pct)
  return direction === 'up' ? 'ahead' : direction === 'down' ? 'behind' : 'level'
}

export function BenchmarkStats({ b }: { b: BenchmarkFigure }) {
  const beta = ratioText(b.beta)
  const correlation = ratioText(b.correlation)
  const excess = pctNumber(b.excess_pct)
  if (beta === null && correlation === null && excess === null && !b.stat_note) return null
  return (
    <div className="flex flex-col gap-1">
      {(beta !== null || correlation !== null || excess !== null) && (
        <dl className="grid grid-cols-3 gap-x-3 text-sm">
          <div className="flex min-w-0 flex-col gap-px">
            <dt className={LABEL}>Beta</dt>
            <dd className="text-[0.9375rem] font-semibold">{beta ?? 'Not shown'}</dd>
          </div>
          <div className="flex min-w-0 flex-col gap-px">
            <dt className={LABEL}>Correlation</dt>
            <dd className="text-[0.9375rem] font-semibold">{correlation ?? 'Not shown'}</dd>
          </div>
          <div className="flex min-w-0 flex-col gap-px">
            <dt className={LABEL}>Excess return</dt>
            <dd className="text-[0.9375rem] font-semibold">
              {excess === null ? (
                'Not shown'
              ) : (
                <span
                  className={cn(
                    'inline-flex flex-wrap items-center gap-x-1',
                    TREND_TEXT[directionOf(excess)]
                  )}
                >
                  <span className="inline-flex items-center gap-1 whitespace-nowrap">
                    <TrendArrow direction={directionOf(excess)} className="size-[0.5625rem]" />
                    {formatPercent(excess)}
                  </span>
                  <span className="text-xs">{excessWord(excess)}</span>
                </span>
              )}
            </dd>
          </div>
        </dl>
      )}
      {b.stat_note && <p className={NOTE}>{b.stat_note}</p>}
    </div>
  )
}
