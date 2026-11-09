import { CircleMinus, CirclePlus } from 'lucide-react'
import { memo } from 'react'
import { istTime } from '@/sudarshan/books/time'
import type { LineView } from './cardLine'
import { cardKey } from './dashboardCache'
import { StatusChip } from './marks'
import { runWords } from './Results'
import type { WidgetCard as Card, ScreenStatus } from './types'
import { Line, ModeChip } from './WidgetCard'
import { OptionsButton } from './WidgetMenu'

const NUMBER = new Intl.NumberFormat('en-IN')

type Series = NonNullable<Card['counts']>['series']

/**
 * Matches at each check today: one bar for each check, 48 dp high, the latest labelled with its
 * figure. No colour carries meaning, so nothing depends on it; a screen reader gets the service's
 * own sentence for the whole strip.
 */
function Strip({ series, words }: { series: Series; words: string }) {
  if (series.length === 0)
    return (
      <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">{words}</p>
    )
  const top = Math.max(1, ...series.map((point) => point.matches))
  const latest = series[series.length - 1]
  return (
    <div className="flex flex-col gap-1">
      <div role="img" aria-label={words} className="flex h-12 items-end gap-px">
        {series.map((point, index) => (
          <span
            key={point.at}
            data-bar={index}
            style={{ height: `${Math.max(6, Math.round((point.matches / top) * 100))}%` }}
            className="min-w-0.5 flex-1 rounded-t-sm bg-[color:var(--brand)]"
          />
        ))}
      </div>
      <p className="flex justify-between text-[0.75rem] font-semibold text-[color:var(--text-secondary)]">
        <span>Matches at each check today</span>
        <span data-latest="true">
          {NUMBER.format(latest.matches)} at {istTime(latest.at).slice(0, 5)}
        </span>
      </p>
    </div>
  )
}

/**
 * One count widget (design 3.8): the matches now as one big figure, the day's entries and exits,
 * the run line, the mode chip for a screen that reads day candles alone, and the strip of matches
 * at each check. It shows no prices, so its line says Checking and never Real-time.
 */
export const CountCard = memo(function CountCard({
  card,
  name,
  where,
  status,
  line,
  onOpenScreen,
  onOptions,
}: {
  card: Card
  name: string
  where: string
  status: ScreenStatus
  line: LineView
  onOpenScreen: (id: number) => void
  onOptions: (card: Card) => void
}) {
  const matches = card.matches ?? card.run?.matches ?? 0
  const counts = card.counts
  return (
    <li
      data-widget={cardKey(card)}
      aria-label={`${name}, ${where}`}
      className="flex flex-col overflow-hidden rounded-[1rem] border bg-card shadow-[var(--shadow-card)]"
    >
      <div className="flex items-start">
        <button
          type="button"
          onClick={() => onOpenScreen(card.screen_id)}
          aria-label={`${name}: open this screen`}
          className="flex min-h-12 min-w-0 flex-1 flex-col gap-1 pt-3 pl-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <span className="flex w-full items-start justify-between gap-2">
            <span className="min-w-0 truncate text-base font-bold">{name}</span>
            <StatusChip status={status} />
          </span>
          <span className="flex flex-wrap items-center gap-2 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            {where}
            <ModeChip mode={card.mode} />
          </span>
        </button>
        <OptionsButton name={name} onOpen={() => onOptions(card)} />
      </div>
      <div className="flex flex-col gap-2 px-4 pt-1 pb-3">
        <Line line={line} />
        <p className="flex items-baseline gap-2">
          <span
            data-count="matches"
            className="sd-display text-[2.5rem] leading-none font-semibold"
          >
            {NUMBER.format(matches)}
          </span>
          <span className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]">
            match now
          </span>
        </p>
        {counts && (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.875rem] font-semibold">
            <span className="inline-flex items-center gap-1">
              <CirclePlus aria-hidden="true" className="size-4" />+
              {NUMBER.format(counts.entered_today)} entered
            </span>
            <span className="inline-flex items-center gap-1">
              <CircleMinus aria-hidden="true" className="size-4" />−
              {NUMBER.format(counts.exited_today)} exited today
            </span>
          </p>
        )}
        <p
          data-run={card.run?.id}
          className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
        >
          {card.run
            ? runWords(card.run)
            : status === 'active'
              ? 'Not checked yet.'
              : 'Not checking.'}
        </p>
        {card.rows_pending ? null : counts ? (
          <Strip series={counts.series} words={counts.series_words} />
        ) : null}
      </div>
    </li>
  )
})
