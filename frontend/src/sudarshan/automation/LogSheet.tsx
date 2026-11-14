// SUDARSHAN M12.1: a strategy's Log. Every alert that reached its link, newest first, with what
// SUDARSHAN did in a sentence and the two delays: TradingView's and SUDARSHAN's own.
import { useInfiniteQuery } from '@tanstack/react-query'
import { Loader2, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { alertsKey, automationApi } from './api'
import { ToneIcon } from './StrategyCard'
import type { AlertLine, Strategy } from './types'
import { toneOf, whenText } from './words'

/** "1.2 s" or "310 ms", as a trader says a short wait. */
export function waitText(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`
}

function meta(line: AlertLine): string {
  const parts = [line.mode === 'live' ? 'Live' : line.mode === 'sandbox' ? 'Sandbox' : null]
  if (line.tv_delay_ms !== null) parts.push(`TradingView took ${waitText(line.tv_delay_ms)}`)
  if (line.answer_ms !== null) parts.push(`SUDARSHAN answered in ${waitText(line.answer_ms)}`)
  return parts.filter(Boolean).join(' · ')
}

export function LogSheet({
  open,
  onClose,
  strategy,
}: {
  open: boolean
  onClose: () => void
  strategy: Strategy | null
}) {
  const id = strategy?.id ?? 0
  const log = useInfiniteQuery({
    queryKey: alertsKey(id),
    queryFn: ({ pageParam }) => automationApi.alerts(id, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.more ? last.alerts.at(-1)?.id : undefined),
    enabled: open && id > 0,
  })
  const lines = log.data?.pages.flatMap((page) => page.alerts) ?? []
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={`Log: ${strategy?.name ?? ''}`}
      description="Every alert that reached its link, newest first"
    >
      {log.isPending ? (
        <Loader2 aria-label="Reading the log" className="mx-auto my-6 size-6 animate-spin" />
      ) : log.isError ? (
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {log.error.message}
        </p>
      ) : lines.length === 0 ? (
        <p className="py-4 text-[0.9375rem] text-[color:var(--text-secondary)]">
          No alert has reached this strategy’s link yet. When TradingView sends one, it shows here
          at once, whatever SUDARSHAN did with it.
        </p>
      ) : (
        <ol className="divide-y divide-[color:var(--divider)]">
          {lines.map((line) => (
            <li
              key={line.id}
              data-verdict={line.verdict}
              className="flex items-start gap-2 py-2.5 text-[0.875rem] leading-snug"
            >
              <ToneIcon tone={toneOf(line.verdict)} />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
                  {whenText(line.received_ms)}
                </span>
                <span className="break-words">{line.sentence}</span>
                <span className="text-[0.78125rem] text-[color:var(--text-secondary)]">
                  {meta(line)}
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {log.hasNextPage && (
        <Button
          variant="outline"
          className="mt-3 h-12 w-full"
          disabled={log.isFetchingNextPage}
          onClick={() => log.fetchNextPage()}
        >
          {log.isFetchingNextPage && <Loader2 aria-hidden="true" className="size-5 animate-spin" />}
          Earlier alerts
        </Button>
      )}
    </BottomSheet>
  )
}
